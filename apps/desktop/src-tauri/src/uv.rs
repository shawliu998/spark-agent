// Shared runner for long uv-sidecar provisioning (jupyter env, science MCP
// env). The old `.output().await` calls were silent and unbounded: a stalled
// download (proxy, TLS inspection, antivirus) left the UI on "Setting up…"
// forever with zero diagnostics. This streams every output line to the
// frontend as a `setup-progress` event and kills the process when it produces
// no output for STALL_SECS, turning a silent hang into a readable error.
use std::collections::VecDeque;
use std::ffi::OsString;
use std::path::Path;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// One line of live provisioning output, shown in Settings while setting up.
#[derive(Clone, serde::Serialize)]
pub struct SetupProgress {
    /// Which provisioning flow the line belongs to ("jupyter" | "science").
    pub task: &'static str,
    pub line: String,
}

/// No output for this long = the download is wedged, not slow: uv prints a
/// line per package/wheel, so even a slow link produces output well within
/// this window. Generous on purpose — killing a genuinely slow download is
/// worse than waiting.
const STALL_SECS: u64 = 600;

/// How many trailing output lines to keep for the error message.
const TAIL_LINES: usize = 12;
const PYPI_INDEX: &str = "https://pypi.org/simple";

fn isolated_uv_args(args: Vec<String>) -> Vec<String> {
    let mut isolated = Vec::with_capacity(args.len() + 1);
    isolated.push("--no-config".to_string());
    isolated.extend(args);
    isolated
}

fn isolated_uv_environment(
    provisioning_home: &Path,
    cache: &Path,
    python_install: &Path,
    safe_path: &str,
    proxy: Vec<(&'static str, String)>,
) -> Vec<(OsString, OsString)> {
    let mut environment = vec![
        (
            OsString::from("HOME"),
            provisioning_home.as_os_str().to_owned(),
        ),
        (OsString::from("PATH"), OsString::from(safe_path)),
        (OsString::from("UV_CACHE_DIR"), cache.as_os_str().to_owned()),
        (
            OsString::from("UV_PYTHON_INSTALL_DIR"),
            python_install.as_os_str().to_owned(),
        ),
        (OsString::from("UV_NO_CONFIG"), OsString::from("1")),
        (
            OsString::from("UV_DEFAULT_INDEX"),
            OsString::from(PYPI_INDEX),
        ),
    ];
    environment.extend(
        proxy
            .into_iter()
            .map(|(key, value)| (OsString::from(key), OsString::from(value))),
    );
    environment
}

/// Keep the last TAIL_LINES lines: uv puts the actual failure reason at the
/// end of its output, and the full log of a 300 MB install is noise.
fn push_tail(tail: &mut VecDeque<String>, line: &str) {
    if tail.len() == TAIL_LINES {
        tail.pop_front();
    }
    tail.push_back(line.to_string());
}

/// Run the bundled uv with live progress. Emits each output line as a
/// `setup-progress` event, fails with the output tail on a non-zero exit, and
/// kills + fails when uv goes silent for STALL_SECS.
pub async fn run_uv(
    app: &AppHandle,
    task: &'static str,
    args: Vec<String>,
    label: &str,
) -> Result<(), String> {
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|error| error.to_string())?
        .join("uv");
    let python_install = app_data.join("runtime").join("uv-python");
    let provisioning_home = app_data.join("runtime").join("provisioning-home");
    for directory in [&cache, &python_install, &provisioning_home] {
        std::fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    }

    #[cfg(not(windows))]
    let safe_path = "/usr/bin:/bin:/usr/sbin:/sbin".to_string();
    #[cfg(windows)]
    let safe_path = std::env::var("PATH").unwrap_or_default();
    let environment = isolated_uv_environment(
        &provisioning_home,
        &cache,
        &python_install,
        &safe_path,
        crate::runtime::provisioning_proxy_env(app),
    );
    let command = app
        .shell()
        .sidecar("uv")
        .map_err(|e| format!("uv sidecar not found: {e}"))?
        .env_clear()
        .envs(environment)
        .args(isolated_uv_args(args));
    let (mut rx, child) = command
        .spawn()
        .map_err(|e| format!("{label} failed to run: {e}"))?;

    let mut tail: VecDeque<String> = VecDeque::new();
    loop {
        let event = match tokio::time::timeout(Duration::from_secs(STALL_SECS), rx.recv()).await {
            Err(_) => {
                let _ = child.kill();
                return Err(format!(
                    "{label} stalled — no output for {} minutes. Check your network/proxy \
                     (needs github.com and pypi.org), and consider excluding the app data \
                     folder from real-time antivirus scanning, then retry.",
                    STALL_SECS / 60
                ));
            }
            // Channel closed without a Terminated event: treat as failure.
            Ok(None) => return Err(format!("{label} exited without a status: {}", last(&tail))),
            Ok(Some(event)) => event,
        };
        match event {
            CommandEvent::Stdout(bytes) | CommandEvent::Stderr(bytes) => {
                // uv writes plain lines when piped; split handles multi-line chunks.
                for line in String::from_utf8_lossy(&bytes).split(['\n', '\r']) {
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }
                    push_tail(&mut tail, line);
                    let _ = app.emit(
                        "setup-progress",
                        SetupProgress {
                            task,
                            line: line.to_string(),
                        },
                    );
                }
            }
            CommandEvent::Error(e) => push_tail(&mut tail, &e),
            CommandEvent::Terminated(status) => {
                return if status.code == Some(0) {
                    Ok(())
                } else {
                    Err(format!("{label} failed: {}", last(&tail)))
                };
            }
            _ => {}
        }
    }
}

fn last(tail: &VecDeque<String>) -> String {
    if tail.is_empty() {
        "(no output)".to_string()
    } else {
        tail.iter().cloned().collect::<Vec<_>>().join("\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    #[test]
    fn tail_keeps_only_the_last_lines() {
        let mut tail = VecDeque::new();
        for i in 0..(TAIL_LINES + 5) {
            push_tail(&mut tail, &format!("line {i}"));
        }
        assert_eq!(tail.len(), TAIL_LINES);
        assert_eq!(tail.front().unwrap(), "line 5");
        assert_eq!(tail.back().unwrap(), &format!("line {}", TAIL_LINES + 4));
    }

    #[test]
    fn last_reports_no_output_when_empty() {
        assert_eq!(last(&VecDeque::new()), "(no output)");
        let mut tail = VecDeque::new();
        push_tail(&mut tail, "error: boom");
        assert!(last(&tail).contains("boom"));
    }

    #[test]
    fn provisioning_disables_config_and_forwards_only_allowlisted_environment() {
        let args = isolated_uv_args(vec!["pip".into(), "install".into(), "pkg==1".into()]);
        assert_eq!(args[0], "--no-config");

        let environment = isolated_uv_environment(
            Path::new("/private/home"),
            Path::new("/private/cache"),
            Path::new("/private/python"),
            "/usr/bin:/bin",
            vec![
                ("HTTPS_PROXY", "http://proxy.invalid:8080".into()),
                ("NO_PROXY", "localhost,127.0.0.1,::1".into()),
            ],
        );
        let keys = environment
            .iter()
            .map(|(key, _)| key.to_string_lossy().to_string())
            .collect::<BTreeSet<_>>();
        assert_eq!(
            keys,
            BTreeSet::from([
                "HOME".to_string(),
                "PATH".to_string(),
                "UV_CACHE_DIR".to_string(),
                "UV_DEFAULT_INDEX".to_string(),
                "UV_NO_CONFIG".to_string(),
                "UV_PYTHON_INSTALL_DIR".to_string(),
                "HTTPS_PROXY".to_string(),
                "NO_PROXY".to_string(),
            ])
        );
        assert!(!keys.iter().any(|key| {
            matches!(
                key.as_str(),
                "UV_INDEX" | "UV_FIND_LINKS" | "UV_CONFIG_FILE" | "PIP_CONFIG_FILE"
            )
        }));
        assert!(environment
            .iter()
            .any(|(key, value)| { key == "UV_DEFAULT_INDEX" && value == PYPI_INDEX }));
    }
}
