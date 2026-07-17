// Pure merge of provider credentials/model into OpenCode config JSON.
// Used by the runtime command, which writes it into an app-private config dir.
use serde_json::{json, Value};

/// User-facing OpenCode permission presets. `full` remains the persisted/API
/// compatibility value for the Autonomous preset; arbitrary user-authored
/// objects remain custom.
pub const MODE_BALANCED: &str = "balanced";
pub const MODE_FULL: &str = "full";
pub const MODE_CUSTOM: &str = "custom";

/// OpenCode accepts JSONC (comments and trailing commas) for `opencode.jsonc`.
/// Parse strict JSON first for precise errors, then the JSON5-compatible
/// superset used for JSONC. Writes are normalized to strict JSON, preserving
/// every data field while intentionally discarding comments.
pub fn parse_config(existing: &str, label: &str) -> Result<Value, String> {
    if existing.trim().is_empty() {
        return Ok(json!({}));
    }
    serde_json::from_str(existing).or_else(|json_error| {
        json5::from_str(existing).map_err(|jsonc_error| {
            format!("invalid {label}: {json_error}; JSONC parse also failed: {jsonc_error}")
        })
    })
}

fn config_object_mut<'a>(
    root: &'a mut Value,
    label: &str,
) -> Result<&'a mut serde_json::Map<String, Value>, String> {
    root.as_object_mut()
        .ok_or_else(|| format!("{label} must be a JSON object"))
}

fn legacy_approve_permission() -> Value {
    // Exact policy shipped before the General Research profile. Recognizing it
    // lets startup migrate Spark-owned defaults without rewriting arbitrary
    // user permission objects.
    json!({
        "*": "ask",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "task": "allow",
        "skill": "allow",
        "lsp": "allow",
        "edit": "ask",
        "bash": "ask",
        "webfetch": "ask",
        "websearch": "ask",
        // The first curated research connector is read-only at the policy
        // boundary: metadata/search and remote reads are allowed, while every
        // download tool (which writes a PDF locally) is unavailable.
        "paper-search_search_*": "allow",
        "paper-search_read_*": "allow",
        "paper-search_download_*": "deny",
        "external_directory": "deny"
    })
}

/// Exact destructive list used by the prior Balanced policy. Keep it stable so
/// Spark-owned configs from that release can be recognized during migration.
const DESTRUCTIVE_BASH: &[&str] = &[
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "truncate",
    "git clean",
    "git reset --hard",
    "sudo",
    "su",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "killall",
    "launchctl",
    "systemctl",
    "crontab",
    "osascript",
    "diskutil",
    "dd",
];

/// Balanced additionally asks before dependency changes, credential access,
/// network/remote operations, and paid or cluster execution.
const BALANCED_BASH: &[&str] = &[
    "pip install",
    "pip3 install",
    "python -m pip install",
    "uv add",
    "uv pip install",
    "npm install",
    "npm i",
    "npm add",
    "pnpm add",
    "pnpm install",
    "yarn add",
    "bun add",
    "conda install",
    "mamba install",
    "brew install",
    "cargo install",
    "gem install",
    "apt install",
    "apt-get install",
    "env",
    "printenv",
    "security",
    "ssh",
    "scp",
    "sftp",
    "rsync",
    "curl",
    "wget",
    "nc",
    "git push",
    "modal",
    "sbatch",
    "aws",
    "gcloud",
    "az",
    "kubectl",
];

/// Autonomous still asks before destructive changes, ambiguous global Python
/// installs, credentials, remote uploads/connections, system package managers,
/// and cloud/cluster execution. Project-local dependency managers remain part
/// of the ordinary research path.
const AUTONOMOUS_ASK_BASH: &[&str] = &[
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "truncate",
    "git clean",
    "git reset --hard",
    "git push",
    "git remote add",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "killall",
    "pip install",
    "pip3 install",
    "python -m pip install",
    "python3 -m pip install",
    "uv tool install",
    "uv python install",
    "conda install",
    "conda create",
    "mamba install",
    "cargo install",
    "gem install",
    "brew install",
    "apt install",
    "apt-get install",
    "dnf install",
    "yum install",
    "pacman",
    "apk add",
    "npm install -g",
    "npm i -g",
    "npm -g install",
    "pnpm add -g",
    "yarn global add",
    "bun add -g",
    "env",
    "printenv",
    "security",
    "ssh",
    "scp",
    "sftp",
    "rsync",
    "nc",
    "telnet",
    "ftp",
    "modal",
    "sbatch",
    "aws",
    "gcloud",
    "az",
    "kubectl",
    "docker push",
    "podman push",
];

/// These operations have no ordinary research justification in a project
/// workspace and are denied rather than delegated to an approval click.
const AUTONOMOUS_DENY_BASH: &[&str] = &[
    "sudo",
    "su",
    "dd",
    "diskutil",
    "mkfs",
    "newfs",
    "launchctl",
    "systemctl",
    "crontab",
    "security export",
    "security dump-keychain",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf $HOME",
    "find / -delete",
];

fn command_rules(tokens: &[&str], action: &str) -> serde_json::Map<String, Value> {
    let mut bash = serde_json::Map::new();
    bash.insert("*".to_string(), json!("allow"));
    for token in tokens {
        bash.insert(format!("{token} *"), json!(action));
        bash.insert(format!("* {token} *"), json!(action));
    }
    bash
}

fn sensitive_shell_rules(bash: &mut serde_json::Map<String, Value>) {
    for pattern in [
        "find * -delete*",
        "* find * -delete*",
        "* .env*",
        "* */.env*",
        "* ~/.ssh/*",
        "* ~/.aws/*",
        "* ~/.config/*",
        "*> ../*",
        "*>> ../*",
        "* > ../*",
        "* >> ../*",
        "cp * ../*",
        "* cp * ../*",
        "mv * ../*",
        "* mv * ../*",
        "tee ../*",
        "* tee ../*",
    ] {
        bash.insert(pattern.to_string(), json!("ask"));
    }
}

fn autonomous_bash_permission() -> Value {
    let mut bash = command_rules(AUTONOMOUS_ASK_BASH, "ask");
    for pattern in [
        "find * -delete*",
        "* find * -delete*",
        "* .env*",
        "* */.env*",
        "curl * -T *",
        "* curl * -T *",
        "curl -T *",
        "* curl -T *",
        "curl * --upload-file *",
        "* curl * --upload-file *",
        "curl --upload-file *",
        "* curl --upload-file *",
        "curl * --data *",
        "* curl * --data *",
        "curl --data *",
        "* curl --data *",
        "curl * --data-binary *",
        "* curl * --data-binary *",
        "curl --data-binary *",
        "* curl --data-binary *",
        "curl * -d *",
        "* curl * -d *",
        "curl -d *",
        "* curl -d *",
        "curl * -X POST *",
        "* curl * -X POST *",
        "curl -X POST *",
        "* curl -X POST *",
        "curl * --request POST *",
        "* curl * --request POST *",
        "curl --request POST *",
        "* curl --request POST *",
        "wget * --post-file *",
        "* wget * --post-file *",
    ] {
        bash.insert(pattern.to_string(), json!("ask"));
    }
    for pattern in [
        "* ~/.ssh/*",
        "* ~/.aws/*",
        "* ~/.config/gcloud/*",
        "* ~/.azure/*",
        "* ~/.kube/*",
        "*> ../*",
        "*>> ../*",
        "* > ../*",
        "* >> ../*",
        "cp * ../*",
        "* cp * ../*",
        "mv * ../*",
        "* mv * ../*",
        "tee ../*",
        "* tee ../*",
        "touch ../*",
        "* touch ../*",
        "mkdir ../*",
        "* mkdir ../*",
        "*> /*",
        "*>> /*",
        "cp * /*",
        "* cp * /*",
        "mv * /*",
        "* mv * /*",
        "tee /*",
        "* tee /*",
        "OPENCODE_PERMISSION=*",
        "* OPENCODE_PERMISSION=*",
        "unset OPENCODE_PERMISSION*",
        "* unset OPENCODE_PERMISSION*",
    ] {
        bash.insert(pattern.to_string(), json!("deny"));
    }
    for token in AUTONOMOUS_DENY_BASH {
        bash.insert(format!("{token} *"), json!("deny"));
        bash.insert(format!("* {token} *"), json!("deny"));
    }

    // Ambiguous global pip installs ask, but the two conventional managed
    // project environments are explicit and remain autonomous.
    for pattern in [
        ".spark/python/bin/pip install *",
        "./.spark/python/bin/pip install *",
        ".venv/bin/pip install *",
        "./.venv/bin/pip install *",
    ] {
        bash.insert(pattern.to_string(), json!("allow"));
    }
    Value::Object(bash)
}

fn prior_balanced_permission() -> Value {
    let mut tokens = DESTRUCTIVE_BASH.to_vec();
    tokens.extend_from_slice(BALANCED_BASH);
    let mut bash = command_rules(&tokens, "ask");
    sensitive_shell_rules(&mut bash);

    json!({
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow"
        },
        "edit": "allow",
        "bash": Value::Object(bash),
        "external_directory": "ask",
        "mcp": "ask",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "skill": "allow"
    })
}

fn prior_manual_approval_permission() -> Value {
    json!({
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow"
        },
        "edit": "allow",
        "apply_patch": "ask",
        "bash": "ask",
        "external_directory": "deny",
        "mcp": "ask",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "ask",
        "websearch": "ask",
        "skill": "allow"
    })
}

fn balanced_permission() -> Value {
    // Root policy requires manual approval for every command, edit, remote
    // request, and dependency change. Unknown native tools ask by default.
    json!({
        "*": "ask",
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow",
            "mcp:*": "ask"
        },
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "lsp": "allow",
        "edit": "ask",
        "apply_patch": "ask",
        "bash": "ask",
        "external_directory": "deny",
        "mcp": "ask",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "ask",
        "websearch": "ask",
        "skill": "allow"
    })
}

fn prior_full_permission() -> Value {
    // Exact Full Access policy shipped before the Autonomous preset. It is
    // recognized only so an existing selection migrates without user action.
    json!({
        "*": "ask",
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow",
            "mcp:*": "ask"
        },
        "edit": "allow",
        "apply_patch": "allow",
        "bash": "ask",
        "external_directory": "deny",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "ask",
        "websearch": "ask",
        "mcp": "ask",
        "skill": "allow"
    })
}

fn full_permission() -> Value {
    // `full` is the storage compatibility alias for Autonomous. Unknown tools
    // still ask, while ordinary project research is allowed explicitly and
    // bash keeps narrow ask/deny edges for destructive or external actions.
    json!({
        "*": "ask",
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow"
        },
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "lsp": "allow",
        "edit": "allow",
        "write": "allow",
        "patch": "allow",
        "multiedit": "allow",
        "apply_patch": "allow",
        "bash": autonomous_bash_permission(),
        "external_directory": "deny",
        "mcp": "allow",
        "paper-search_*": "allow",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "codesearch": "allow",
        "skill": "allow",
        "task": "allow",
        "todowrite": "allow",
        "todoread": "allow",
        "plan_enter": "allow",
        "plan_exit": "allow"
    })
}

fn full_permission_with_mcp(mcp: Option<&Value>) -> Value {
    let mut permission = full_permission()
        .as_object()
        .expect("Autonomous permission is an object")
        .clone();
    if let Some(servers) = mcp.and_then(Value::as_object) {
        for server in servers.keys() {
            // OpenCode exposes MCP permissions under the concrete tool id
            // (`server_tool`). Seed every configured server prefix so custom
            // credential-free research tools do not fall back to wildcard ask.
            permission.insert(format!("{server}_*"), json!("allow"));
        }
    }
    Value::Object(permission)
}

fn is_full_permission(permission: &Value, mcp: Option<&Value>) -> bool {
    if permission == &full_permission()
        || permission == &prior_full_permission()
        || is_legacy_full(permission)
    {
        return true;
    }
    let Some(mut normalized) = permission.as_object().cloned() else {
        return false;
    };
    if let Some(servers) = mcp.and_then(Value::as_object) {
        let base = full_permission();
        let base = base
            .as_object()
            .expect("Autonomous permission is an object");
        for server in servers.keys() {
            let key = format!("{server}_*");
            if !base.contains_key(&key) && normalized.get(&key) == Some(&json!("allow")) {
                normalized.remove(&key);
            }
        }
    }
    Value::Object(normalized) == full_permission()
}

fn legacy_full_permission() -> Value {
    json!({ "*": "allow" })
}

fn is_legacy_full(permission: &Value) -> bool {
    permission == &legacy_full_permission() || permission == &json!({})
}

/// Exact native JSON injected after global/project config. A custom policy is
/// preserved on disk but runs under the Balanced overlay until Spark can name
/// and validate it as a selectable preset.
pub fn effective_permission_mode(existing: &str) -> Result<&'static str, String> {
    match permission_mode_of(existing)? {
        Some(MODE_FULL) => Ok(MODE_FULL),
        Some(MODE_BALANCED | MODE_CUSTOM) | None => Ok(MODE_BALANCED),
        Some(_) => unreachable!("permission_mode_of returns only known modes"),
    }
}

pub fn effective_permission_floor_json(existing: &str) -> Result<String, String> {
    let root = parse_config(existing, "OpenCode config")?;
    let permission = match effective_permission_mode(existing)? {
        MODE_FULL => full_permission_with_mcp(root.get("mcp")),
        MODE_BALANCED => balanced_permission(),
        _ => unreachable!("effective_permission_mode returns a selectable mode"),
    };
    serde_json::to_string(&permission).map_err(|error| error.to_string())
}

/// Apply an explicit native OpenCode permission preset; unrelated config keys
/// (providers, models, MCP, user options) are preserved structurally.
pub fn set_permission_mode(existing: &str, mode: &str) -> Result<String, String> {
    let mut root = parse_config(existing, "existing OpenCode config")?;
    let permission = match mode {
        MODE_BALANCED => balanced_permission(),
        MODE_FULL => full_permission_with_mcp(root.get("mcp")),
        other => return Err(format!("unknown approval mode \"{other}\"")),
    };
    config_object_mut(&mut root, "existing OpenCode config")?
        .insert("permission".to_string(), permission);
    serde_json::to_string_pretty(&root).map_err(|e| e.to_string())
}

/// The approval mode a config encodes: None when the `permission` key was
/// never written or is a custom policy not represented by the two UI presets.
pub fn permission_mode_of(existing: &str) -> Result<Option<&'static str>, String> {
    let root = parse_config(existing, "OpenCode config")?;
    let root = root
        .as_object()
        .ok_or_else(|| "OpenCode config must be a JSON object".to_string())?;
    let Some(permission) = root.get("permission") else {
        return Ok(None);
    };
    if permission == &balanced_permission()
        || permission == &prior_manual_approval_permission()
        || permission == &prior_balanced_permission()
        || permission == &legacy_approve_permission()
    {
        Ok(Some(MODE_BALANCED))
    } else if is_full_permission(permission, root.get("mcp")) {
        Ok(Some(MODE_FULL))
    } else {
        Ok(Some(MODE_CUSTOM))
    }
}

/// Merge the bundled app-private profile into an existing OpenCode config.
/// Every explicit existing field wins. Permission is seeded when missing;
/// exact Spark-owned legacy Balanced/manual and Full presets migrate to their
/// current equivalents. Arbitrary custom permission objects win at rest.
pub fn merge_profile_defaults(existing: &str, template: &str) -> Result<String, String> {
    let mut root = parse_config(existing, "existing OpenCode config")?;
    let defaults = parse_config(template, "bundled OpenCode profile")?;
    let root_obj = config_object_mut(&mut root, "existing OpenCode config")?;
    let autonomous_permission = full_permission_with_mcp(root_obj.get("mcp"));
    let defaults_obj = defaults
        .as_object()
        .ok_or_else(|| "bundled OpenCode profile must be a JSON object".to_string())?;

    for (key, value) in defaults_obj {
        match root_obj.get(key) {
            None => {
                root_obj.insert(key.clone(), value.clone());
            }
            Some(current) if key == "permission" => {
                if current == &legacy_approve_permission()
                    || current == &prior_manual_approval_permission()
                    || current == &prior_balanced_permission()
                {
                    root_obj.insert(key.clone(), balanced_permission());
                } else if is_full_permission(current, root_obj.get("mcp")) {
                    root_obj.insert(key.clone(), autonomous_permission.clone());
                }
            }
            Some(_) => {}
        }
    }
    serde_json::to_string_pretty(&root).map_err(|e| e.to_string())
}

/// Merge provider credentials/model into existing OpenCode config JSON.
/// Empty fields are left untouched; existing unrelated keys are preserved.
pub fn merge_config(
    existing: &str,
    provider: &str,
    api_key: &str,
    model: &str,
    base_url: Option<&str>,
) -> Result<String, String> {
    let mut root = parse_config(existing, "existing OpenCode config")?;
    let obj = config_object_mut(&mut root, "existing OpenCode config")?;

    if !model.is_empty() {
        obj.insert("model".to_string(), json!(model));
    }

    if !provider.is_empty() {
        let providers = obj.entry("provider").or_insert_with(|| json!({}));
        let pobj = providers
            .as_object_mut()
            .ok_or_else(|| "existing OpenCode config provider must be a JSON object".to_string())?;
        let entry = pobj.entry(provider).or_insert_with(|| json!({}));
        let provider_entry = entry.as_object_mut().ok_or_else(|| {
            format!("existing OpenCode provider {provider:?} must be a JSON object")
        })?;
        let options = provider_entry.entry("options").or_insert_with(|| json!({}));
        let oobj = options.as_object_mut().ok_or_else(|| {
            format!("existing OpenCode provider {provider:?} options must be a JSON object")
        })?;
        if !api_key.is_empty() {
            oobj.insert("apiKey".to_string(), json!(api_key));
        }
        if let Some(b) = base_url {
            if !b.is_empty() {
                oobj.insert("baseURL".to_string(), json!(b));
            }
        }
    }

    serde_json::to_string_pretty(&root).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_provider_key_model_into_empty_config() {
        let out = merge_config(
            "",
            "anthropic",
            "sk-test",
            "anthropic/claude-sonnet-4-5",
            None,
        )
        .unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["model"], "anthropic/claude-sonnet-4-5");
        assert_eq!(v["provider"]["anthropic"]["options"]["apiKey"], "sk-test");
    }

    #[test]
    fn preserves_existing_unrelated_config() {
        let existing = r#"{"theme":"dark","provider":{"openai":{"options":{"apiKey":"old"}}}}"#;
        let out = merge_config(existing, "anthropic", "sk-new", "", None).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["theme"], "dark");
        assert_eq!(v["provider"]["openai"]["options"]["apiKey"], "old");
        assert_eq!(v["provider"]["anthropic"]["options"]["apiKey"], "sk-new");
    }

    #[test]
    fn sets_base_url_when_provided() {
        let out = merge_config("", "openai", "k", "openai/gpt-4o", Some("https://x/v1")).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(
            v["provider"]["openai"]["options"]["baseURL"],
            "https://x/v1"
        );
    }

    #[test]
    fn balanced_mode_requires_approval_for_every_mutation_command_and_remote_tool() {
        let out = set_permission_mode("", MODE_BALANCED).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["permission"]["*"], "ask");
        assert_eq!(v["permission"]["read"]["*"], "allow");
        assert_eq!(v["permission"]["read"]["*.env"], "ask");
        assert_eq!(v["permission"]["read"]["*.env.*"], "ask");
        assert_eq!(v["permission"]["read"]["*.env.example"], "allow");
        assert_eq!(v["permission"]["read"]["mcp:*"], "ask");
        assert_eq!(v["permission"]["glob"], "allow");
        assert_eq!(v["permission"]["grep"], "allow");
        assert_eq!(v["permission"]["list"], "allow");
        assert_eq!(v["permission"]["lsp"], "allow");
        assert_eq!(v["permission"]["edit"], "ask");
        assert_eq!(v["permission"]["apply_patch"], "ask");
        assert_eq!(v["permission"]["bash"], "ask");
        assert_eq!(v["permission"]["external_directory"], "deny");
        assert_eq!(v["permission"]["mcp"], "ask");
        assert_eq!(v["permission"]["doom_loop"], "ask");
        assert_eq!(v["permission"]["question"], "allow");
        assert_eq!(v["permission"]["webfetch"], "ask");
        assert_eq!(v["permission"]["websearch"], "ask");
        assert_eq!(v["permission"]["skill"], "allow");
    }

    #[test]
    fn full_storage_alias_encodes_the_autonomous_native_policy() {
        let out = set_permission_mode("", MODE_FULL).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["permission"]["*"], "ask");
        assert_eq!(v["permission"]["edit"], "allow");
        assert_eq!(v["permission"]["write"], "allow");
        assert_eq!(v["permission"]["patch"], "allow");
        assert_eq!(v["permission"]["apply_patch"], "allow");
        assert_eq!(v["permission"]["bash"]["*"], "allow");
        assert_eq!(v["permission"]["bash"]["rm *"], "ask");
        assert_eq!(v["permission"]["bash"]["git push *"], "ask");
        assert_eq!(v["permission"]["bash"]["pip install *"], "ask");
        assert_eq!(v["permission"]["bash"]["sudo *"], "deny");
        assert_eq!(v["permission"]["bash"]["systemctl *"], "deny");
        assert_eq!(
            v["permission"]["bash"][".spark/python/bin/pip install *"],
            "allow"
        );
        assert_eq!(v["permission"]["webfetch"], "allow");
        assert_eq!(v["permission"]["websearch"], "allow");
        assert_eq!(v["permission"]["mcp"], "allow");
        assert_eq!(v["permission"]["paper-search_*"], "allow");
        assert_eq!(v["permission"]["skill"], "allow");
        assert_eq!(v["permission"]["task"], "allow");
        assert_eq!(v["permission"]["todowrite"], "allow");
        assert_eq!(v["permission"]["external_directory"], "deny");
        assert_eq!(v["permission"]["read"]["*.env"], "ask");
    }

    #[test]
    fn set_permission_mode_preserves_unrelated_keys() {
        let existing =
            r#"{"model":"anthropic/claude","provider":{"openai":{"options":{"apiKey":"k"}}}}"#;
        let out = set_permission_mode(existing, MODE_BALANCED).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["model"], "anthropic/claude");
        assert_eq!(v["provider"]["openai"]["options"]["apiKey"], "k");
    }

    #[test]
    fn autonomous_allows_each_configured_mcp_server_prefix() {
        let existing = r#"{
          "mcp": {
            "paper-search": {"type":"local","command":["paper-search"]},
            "lab": {"type":"remote","url":"https://example.com/mcp"}
          }
        }"#;
        let out = set_permission_mode(existing, MODE_FULL).unwrap();
        let value: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(value["permission"]["paper-search_*"], "allow");
        assert_eq!(value["permission"]["lab_*"], "allow");
        assert_eq!(value["mcp"]["lab"]["url"], "https://example.com/mcp");
        assert_eq!(permission_mode_of(&out).unwrap(), Some(MODE_FULL));
        let floor: Value =
            serde_json::from_str(&effective_permission_floor_json(&out).unwrap()).unwrap();
        assert_eq!(floor["lab_*"], "allow");

        let mut with_new_server = value;
        with_new_server["mcp"]["new-lab"] = json!({
            "type": "remote",
            "url": "https://example.com/new-mcp"
        });
        let changed = with_new_server.to_string();
        assert_eq!(permission_mode_of(&changed).unwrap(), Some(MODE_FULL));
        let changed_floor: Value =
            serde_json::from_str(&effective_permission_floor_json(&changed).unwrap()).unwrap();
        assert_eq!(changed_floor["new-lab_*"], "allow");
    }

    #[test]
    fn set_permission_mode_rejects_unknown_mode() {
        assert!(set_permission_mode("", "off").is_err());
    }

    #[test]
    fn permission_mode_of_detects_each_state() {
        // Never configured (first run) — profile merging seeds the default.
        assert_eq!(permission_mode_of("").unwrap(), None);
        assert_eq!(permission_mode_of(r#"{"model":"m"}"#).unwrap(), None);
        let balanced = set_permission_mode("", MODE_BALANCED).unwrap();
        assert_eq!(permission_mode_of(&balanced).unwrap(), Some(MODE_BALANCED));
        let full_current = set_permission_mode("", MODE_FULL).unwrap();
        assert_eq!(permission_mode_of(&full_current).unwrap(), Some(MODE_FULL));
        let prior_full = json!({"permission": prior_full_permission()}).to_string();
        assert_eq!(permission_mode_of(&prior_full).unwrap(), Some(MODE_FULL));
        let full = r#"{"permission":{"*":"allow"}}"#;
        assert_eq!(permission_mode_of(full).unwrap(), Some(MODE_FULL));
        assert_eq!(
            permission_mode_of(r#"{"permission":{}}"#).unwrap(),
            Some(MODE_FULL),
            "legacy Full Access used an empty permission object"
        );
        assert_eq!(
            permission_mode_of(r#"{"permission":{"edit":"allow"}}"#).unwrap(),
            Some(MODE_CUSTOM)
        );
        assert_eq!(
            permission_mode_of(r#"{"permission":{"bash":"deny"}}"#).unwrap(),
            Some(MODE_CUSTOM)
        );
    }

    #[test]
    fn profile_defaults_preserve_every_explicit_user_value() {
        let existing = r#"{
          "default_agent": "my-agent",
          "model": "acme/custom",
          "provider": {"acme": {"options": {"apiKey": "secret", "baseURL": "https://x"}}},
          "mcp": {"lab": {"type": "local", "command": ["lab-mcp"]}},
          "permission": {"bash": "deny"},
          "custom": {"nested": true}
        }"#;
        let template = r#"{
          "default_agent": "research",
          "share": "disabled",
          "autoupdate": false,
          "permission": {"*": "allow"}
        }"#;
        let out = merge_profile_defaults(existing, template).unwrap();
        let value: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(value["default_agent"], "my-agent");
        assert_eq!(value["model"], "acme/custom");
        assert_eq!(value["provider"]["acme"]["options"]["apiKey"], "secret");
        assert_eq!(value["mcp"]["lab"]["command"][0], "lab-mcp");
        assert_eq!(value["permission"], json!({ "bash": "deny" }));
        assert_eq!(value["custom"]["nested"], true);
        assert_eq!(value["share"], "disabled");
        assert_eq!(value["autoupdate"], false);
    }

    #[test]
    fn profile_defaults_migrate_exact_legacy_permissions_only() {
        let template = json!({
            "default_agent": "research",
            "permission": balanced_permission(),
        });
        for permission in [
            legacy_approve_permission(),
            prior_manual_approval_permission(),
            prior_balanced_permission(),
        ] {
            let existing = json!({
                "model": "keep/me",
                "permission": permission,
            });
            let out = merge_profile_defaults(&existing.to_string(), &template.to_string()).unwrap();
            let value: Value = serde_json::from_str(&out).unwrap();
            assert_eq!(value["model"], "keep/me");
            assert_eq!(value["default_agent"], "research");
            assert_eq!(value["permission"], balanced_permission());
        }
    }

    #[test]
    fn profile_merge_migrates_legacy_full_choice_to_autonomous() {
        let full = r#"{"model":"keep/me","permission":{"*":"allow"}}"#;
        let template = json!({
            "default_agent": "research",
            "permission": balanced_permission(),
        });
        let restarted = merge_profile_defaults(full, &template.to_string()).unwrap();
        let value: Value = serde_json::from_str(&restarted).unwrap();
        assert_eq!(value["model"], "keep/me");
        assert_eq!(value["permission"], full_permission());
        assert_eq!(permission_mode_of(&restarted).unwrap(), Some(MODE_FULL));
    }

    #[test]
    fn profile_merge_migrates_prior_full_access_policy_to_autonomous() {
        let existing = json!({
            "model": "keep/me",
            "permission": prior_full_permission(),
        });
        let template = json!({"permission": balanced_permission()});
        let merged = merge_profile_defaults(&existing.to_string(), &template.to_string()).unwrap();
        let value: Value = serde_json::from_str(&merged).unwrap();
        assert_eq!(value["model"], "keep/me");
        assert_eq!(value["permission"], full_permission());
        assert_eq!(permission_mode_of(&merged).unwrap(), Some(MODE_FULL));
    }

    #[test]
    fn profile_merge_migrates_legacy_empty_full_across_restart() {
        let template = json!({"permission": balanced_permission()}).to_string();
        let existing = json!({"permission": {}}).to_string();
        let merged = merge_profile_defaults(&existing, &template).unwrap();
        let value: Value = serde_json::from_str(&merged).unwrap();
        assert_eq!(value["permission"], full_permission());
        assert_eq!(permission_mode_of(&merged).unwrap(), Some(MODE_FULL));
    }

    #[test]
    fn jsonc_comments_and_trailing_commas_are_accepted_and_preserved_semantically() {
        let existing = r#"{
          // Keep the user's selected model and custom policy.
          "model": "acme/custom",
          "permission": { "bash": "deny", },
        }"#;
        let template = r#"{"default_agent":"research","permission":{"*":"ask"}}"#;
        let out = merge_profile_defaults(existing, template).unwrap();
        let value: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(value["model"], "acme/custom");
        assert_eq!(value["default_agent"], "research");
        assert_eq!(value["permission"]["bash"], "deny");
        assert_eq!(permission_mode_of(existing).unwrap(), Some(MODE_CUSTOM));
    }

    #[test]
    fn malformed_or_non_object_configs_fail_closed() {
        assert!(merge_profile_defaults("[1]", "{}").is_err());
        assert!(merge_config("[1]", "openai", "secret", "", None).is_err());
        assert!(set_permission_mode("[1]", MODE_BALANCED).is_err());
        assert!(permission_mode_of("{/* unterminated").is_err());
    }

    #[test]
    fn provider_merge_rejects_non_object_user_fields_instead_of_overwriting_them() {
        assert!(
            merge_config(r#"{"provider":"custom"}"#, "openai", "secret", "", None)
                .unwrap_err()
                .contains("provider must be a JSON object")
        );
        assert!(merge_config(
            r#"{"provider":{"openai":"custom"}}"#,
            "openai",
            "secret",
            "",
            None,
        )
        .unwrap_err()
        .contains("provider \"openai\" must be a JSON object"));
        assert!(merge_config(
            r#"{"provider":{"openai":{"options":"custom"}}}"#,
            "openai",
            "secret",
            "",
            None,
        )
        .unwrap_err()
        .contains("options must be a JSON object"));
    }

    #[test]
    fn bundled_profile_and_launch_overlay_match_each_native_preset() {
        let template: Value = serde_json::from_str(include_str!(
            "../../../../runtime/opencode-profile/opencode.json"
        ))
        .unwrap();
        assert_eq!(template["permission"], balanced_permission());
        assert_eq!(
            serde_json::from_str::<Value>(&effective_permission_floor_json("{}").unwrap()).unwrap(),
            balanced_permission()
        );
        let full = set_permission_mode("{}", MODE_FULL).unwrap();
        assert_eq!(
            serde_json::from_str::<Value>(&effective_permission_floor_json(&full).unwrap())
                .unwrap(),
            full_permission()
        );
        let custom = r#"{"permission":{"bash":"deny"}}"#;
        assert_eq!(
            serde_json::from_str::<Value>(&effective_permission_floor_json(custom).unwrap())
                .unwrap(),
            balanced_permission()
        );
    }
}
