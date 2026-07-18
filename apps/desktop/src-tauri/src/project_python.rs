//! Per-project Python environment for autonomous data and notebook research.
//!
//! This reuses the bundled uv provisioning boundary. It intentionally does not
//! introduce another environment manager or install anything globally.

use std::path::{Path, PathBuf};
use tauri::AppHandle;

const PROJECT_PYTHON_PACKAGES: &[&str] = &[
    "jupyterlab==4.4.1",
    "ipykernel==6.29.5",
    "nbformat==5.10.4",
    "nbconvert==7.16.6",
    "numpy==2.2.5",
    "pandas==2.2.3",
    "scipy==1.15.2",
    "matplotlib==3.10.3",
    "scikit-learn==1.6.1",
    "statsmodels==0.14.4",
];

#[derive(Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectPythonStatus {
    installed: bool,
    path: String,
}

fn ensure_managed_directory(path: &Path, workspace: &Path, label: &str) -> Result<(), String> {
    if path.exists() {
        let metadata = std::fs::symlink_metadata(path)
            .map_err(|error| format!("could not inspect {label}: {error}"))?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err(format!(
                "{label} must be a real directory inside the project"
            ));
        }
        let canonical = path
            .canonicalize()
            .map_err(|error| format!("could not resolve {label}: {error}"))?;
        if !canonical.starts_with(workspace) {
            return Err(format!("{label} escaped the active project"));
        }
        return Ok(());
    }
    std::fs::create_dir(path).map_err(|error| format!("could not create {label}: {error}"))
}

fn project_environment(workspace: &Path) -> Result<PathBuf, String> {
    let workspace = workspace
        .canonicalize()
        .map_err(|error| format!("could not resolve the active project: {error}"))?;
    let spark = workspace.join(".spark");
    ensure_managed_directory(&spark, &workspace, ".spark")?;
    let environment = spark.join("python");
    if environment.exists() {
        ensure_managed_directory(&environment, &workspace, ".spark/python")?;
    }
    Ok(environment)
}

fn environment_python(environment: &Path) -> PathBuf {
    #[cfg(windows)]
    return environment.join("Scripts").join("python.exe");
    #[cfg(not(windows))]
    return environment.join("bin").join("python");
}

fn status_for(workspace: &Path) -> Result<ProjectPythonStatus, String> {
    let path = project_environment(workspace)?;
    let installed = environment_python(&path).is_file();
    Ok(ProjectPythonStatus {
        installed,
        path: path.to_string_lossy().to_string(),
    })
}

#[tauri::command(async)]
pub fn project_python_status(app: AppHandle) -> Result<ProjectPythonStatus, String> {
    status_for(&crate::runtime::workspace_dir(&app)?)
}

#[tauri::command]
pub async fn setup_project_python(app: AppHandle) -> Result<ProjectPythonStatus, String> {
    let workspace = crate::runtime::workspace_dir(&app)?;
    let environment = project_environment(&workspace)?;
    crate::uv::run_uv(
        &app,
        "project-python",
        vec![
            "venv".into(),
            environment.to_string_lossy().to_string(),
            "--python".into(),
            "3.12".into(),
            "--allow-existing".into(),
        ],
        "uv project venv",
    )
    .await?;

    let python = environment_python(&environment);
    if !python.is_file() {
        return Err("project Python environment did not create an interpreter".into());
    }
    let mut arguments = vec![
        "pip".to_string(),
        "install".to_string(),
        "--python".to_string(),
        python.to_string_lossy().to_string(),
    ];
    arguments.extend(
        PROJECT_PYTHON_PACKAGES
            .iter()
            .map(|package| package.to_string()),
    );
    crate::uv::run_uv(
        &app,
        "project-python",
        arguments,
        "uv project package install",
    )
    .await?;

    let lock = environment
        .parent()
        .ok_or_else(|| "project environment has no .spark parent".to_string())?
        .join("python-packages.lock");
    std::fs::write(&lock, format!("{}\n", PROJECT_PYTHON_PACKAGES.join("\n")))
        .map_err(|error| format!("could not record project Python packages: {error}"))?;
    status_for(&workspace)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temporary_project(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "spark-project-python-{label}-{}-{nonce}",
            std::process::id()
        ));
        std::fs::create_dir(&root).unwrap();
        root
    }

    #[test]
    fn project_environment_is_scoped_and_has_a_complete_pinned_package_set() {
        let project = temporary_project("scoped");
        let environment = project_environment(&project).unwrap();
        assert_eq!(
            environment,
            project.canonicalize().unwrap().join(".spark/python")
        );
        assert!(project.join(".spark").is_dir());
        assert!(!environment.exists());
        assert!(PROJECT_PYTHON_PACKAGES
            .iter()
            .all(|package| package.split_once("==").is_some()));
        for package in [
            "nbconvert",
            "numpy",
            "pandas",
            "scipy",
            "matplotlib",
            "scikit-learn",
            "statsmodels",
        ] {
            assert!(PROJECT_PYTHON_PACKAGES
                .iter()
                .any(|specification| specification.starts_with(&format!("{package}=="))));
        }
        std::fs::remove_dir_all(project).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn project_environment_rejects_a_symlinked_spark_directory() {
        use std::os::unix::fs::symlink;
        let project = temporary_project("symlink");
        let outside = temporary_project("outside");
        symlink(&outside, project.join(".spark")).unwrap();
        let error = project_environment(&project).unwrap_err();
        assert!(error.contains("real directory"));
        std::fs::remove_file(project.join(".spark")).unwrap();
        std::fs::remove_dir_all(project).unwrap();
        std::fs::remove_dir_all(outside).unwrap();
    }
}
