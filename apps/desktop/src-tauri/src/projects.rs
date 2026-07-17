// Folder-backed Spark projects. The project file deliberately contains only
// portable project metadata; OpenCode remains the owner of conversations.
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::{path::BaseDirectory, AppHandle, Manager};

const PROJECT_SCHEMA_VERSION: u32 = 1;
const PROJECT_FILE: &str = "project.json";
const RECENTS_FILE: &str = "recent-projects.json";

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMetadata {
    pub schema_version: u32,
    pub title: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub template: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub starter_prompt: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_session_id: Option<String>,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProjectSummary {
    pub path: String,
    #[serde(flatten)]
    pub metadata: ProjectMetadata,
    pub last_opened_at: String,
}

fn now() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        .to_string()
}

fn spark_dir(root: &Path) -> PathBuf {
    root.join(".spark")
}

fn project_file(root: &Path) -> PathBuf {
    spark_dir(root).join(PROJECT_FILE)
}

fn runtime_root(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("runtime"))
}

fn recent_file(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(runtime_root(app)?.join(RECENTS_FILE))
}

fn write_json<T: serde::Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let parent = path.parent().ok_or("project metadata has no parent")?;
    std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let bytes = serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?;
    let temporary = path.with_extension("json.tmp");
    std::fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
    std::fs::rename(&temporary, path).map_err(|error| error.to_string())
}

fn safe_folder_name(title: &str) -> Result<&str, String> {
    let title = title.trim();
    if title.is_empty() || title == "." || title == ".." || title.contains(['/', '\\']) {
        return Err("project title must be a single folder name".into());
    }
    Ok(title)
}

fn template_prompt(template: &str) -> Result<Option<(&'static str, &'static str)>, String> {
    match template {
        "blank" => Ok(None),
        "literature-review" => Ok(Some((
            "Literature review",
            "Start a General Research literature review for this project. Define the question, search multiple sources, verify stable identifiers, save references, and write a concise synthesis with limitations.",
        ))),
        "data-analysis" => Ok(Some((
            "Data analysis",
            "Start a General Research data analysis. Inspect the project data, document data quality and assumptions, write and run the analysis, save figures and tables, then report results and limitations.",
        ))),
        "computational-study" => Ok(Some((
            "Computational study",
            "Start a General Research computational study. Scope the research question, design a reproducible method, implement and run the code, validate results, and write a report with limitations.",
        ))),
        _ => Err("unknown research template".into()),
    }
}

fn scaffold(root: &Path, template: &str) -> Result<Option<String>, String> {
    for directory in [
        "papers",
        "data/raw",
        "data/processed",
        "scripts",
        "notebooks",
        "figures",
        "tables",
        "reports",
        "references",
        "notes",
    ] {
        std::fs::create_dir_all(root.join(directory)).map_err(|error| error.to_string())?;
    }
    let Some((heading, prompt)) = template_prompt(template)? else {
        return Ok(None);
    };
    let starter = format!("# {heading}\n\n{prompt}\n");
    let path = spark_dir(root).join("general-research-starter.md");
    std::fs::create_dir_all(spark_dir(root)).map_err(|error| error.to_string())?;
    std::fs::write(path, &starter).map_err(|error| error.to_string())?;
    Ok(Some(prompt.to_string()))
}

fn default_metadata(root: &Path) -> ProjectMetadata {
    let timestamp = now();
    ProjectMetadata {
        schema_version: PROJECT_SCHEMA_VERSION,
        title: root
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| !name.trim().is_empty())
            .unwrap_or("Research project")
            .to_string(),
        description: None,
        template: None,
        starter_prompt: None,
        created_at: timestamp.clone(),
        updated_at: timestamp,
        last_session_id: None,
    }
}

fn read_or_adopt(root: &Path) -> Result<ProjectMetadata, String> {
    let file = project_file(root);
    match std::fs::read(&file) {
        Ok(contents) => {
            let metadata: ProjectMetadata = serde_json::from_slice(&contents)
                .map_err(|error| format!("invalid .spark/project.json: {error}"))?;
            if metadata.schema_version != PROJECT_SCHEMA_VERSION {
                return Err(format!(
                    "unsupported project schema version: {}",
                    metadata.schema_version
                ));
            }
            Ok(metadata)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let metadata = default_metadata(root);
            write_json(&file, &metadata)?;
            Ok(metadata)
        }
        Err(error) => Err(error.to_string()),
    }
}

fn load_recents(app: &AppHandle) -> Result<Vec<ProjectSummary>, String> {
    load_recents_file(&recent_file(app)?)
}

fn load_recents_file(file: &Path) -> Result<Vec<ProjectSummary>, String> {
    match std::fs::read(&file) {
        Ok(contents) => serde_json::from_slice(&contents).map_err(|error| error.to_string()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(error) => Err(error.to_string()),
    }
}

fn remember_in(
    recents: &mut Vec<ProjectSummary>,
    path: String,
    metadata: ProjectMetadata,
    last_opened_at: String,
) -> ProjectSummary {
    let summary = ProjectSummary {
        path: path.clone(),
        metadata,
        last_opened_at,
    };
    recents.retain(|entry| entry.path != path);
    recents.insert(0, summary.clone());
    recents.truncate(20);
    summary
}

fn remember(
    app: &AppHandle,
    root: &Path,
    metadata: ProjectMetadata,
) -> Result<ProjectSummary, String> {
    let path = root.to_string_lossy().to_string();
    let mut recents = load_recents(app)?;
    let summary = remember_in(&mut recents, path, metadata, now());
    write_json(&recent_file(app)?, &recents)?;
    Ok(summary)
}

fn open_root(app: &AppHandle, path: String) -> Result<ProjectSummary, String> {
    let root = PathBuf::from(path);
    if !root.is_absolute() || !root.is_dir() {
        return Err("project folder must be an existing absolute directory".into());
    }
    let root = root.canonicalize().map_err(|error| error.to_string())?;
    let metadata = read_or_adopt(&root)?;
    remember(app, &root, metadata)
}

#[tauri::command]
pub fn create_project(
    app: AppHandle,
    parent: String,
    title: String,
    template: String,
) -> Result<ProjectSummary, String> {
    let parent = PathBuf::from(parent);
    if !parent.is_absolute() || !parent.is_dir() {
        return Err("project location must be an existing absolute directory".into());
    }
    let root = parent.join(safe_folder_name(&title)?);
    if root.exists() {
        return Err("a project folder with this name already exists".into());
    }
    std::fs::create_dir(&root).map_err(|error| error.to_string())?;
    let result = (|| {
        let starter_prompt = scaffold(&root, &template)?;
        let timestamp = now();
        let metadata = ProjectMetadata {
            schema_version: PROJECT_SCHEMA_VERSION,
            title: title.trim().to_string(),
            description: None,
            template: (template != "blank").then_some(template),
            starter_prompt,
            created_at: timestamp.clone(),
            updated_at: timestamp,
            last_session_id: None,
        };
        write_json(&project_file(&root), &metadata)?;
        remember(
            &app,
            &root.canonicalize().map_err(|error| error.to_string())?,
            metadata,
        )
    })();
    if result.is_err() {
        let _ = std::fs::remove_dir_all(&root);
    }
    result
}

#[tauri::command]
pub fn open_project(app: AppHandle, path: String) -> Result<ProjectSummary, String> {
    open_root(&app, path)
}

#[tauri::command]
pub fn list_recent_projects(app: AppHandle) -> Result<Vec<ProjectSummary>, String> {
    let original = load_recents(&app)?;
    let mut valid = Vec::new();
    for entry in &original {
        let root = PathBuf::from(&entry.path);
        if !root.is_dir() {
            continue;
        }
        let root = match root.canonicalize() {
            Ok(root) => root,
            Err(_) => continue,
        };
        let metadata = match read_or_adopt(&root) {
            Ok(metadata) => metadata,
            Err(_) => continue,
        };
        valid.push(ProjectSummary {
            path: root.to_string_lossy().to_string(),
            metadata,
            last_opened_at: entry.last_opened_at,
        });
    }
    if valid.len() != original.len() {
        write_json(&recent_file(&app)?, &valid)?;
    }
    Ok(valid)
}

#[tauri::command]
pub fn remove_recent_project(app: AppHandle, path: String) -> Result<(), String> {
    let mut recents = load_recents(&app)?;
    recents.retain(|entry| entry.path != path);
    write_json(&recent_file(&app)?, &recents)
}

#[tauri::command]
pub fn update_project_last_session(
    app: AppHandle,
    path: String,
    session_id: String,
) -> Result<(), String> {
    let root = PathBuf::from(path);
    if !root.is_absolute() || !root.is_dir() {
        return Err("project folder must be an existing absolute directory".into());
    }
    let root = root.canonicalize().map_err(|error| error.to_string())?;
    let mut metadata = read_or_adopt(&root)?;
    metadata.last_session_id = (!session_id.trim().is_empty()).then_some(session_id);
    metadata.updated_at = now();
    write_json(&project_file(&root), &metadata)?;
    remember(&app, &root, metadata)?;
    Ok(())
}

#[tauri::command]
pub fn open_demo_project(app: AppHandle) -> Result<ProjectSummary, String> {
    let root = crate::runtime::base_workspace_dir(&app)?.join("spark-agent-climate-demo");
    std::fs::create_dir_all(&root).map_err(|error| error.to_string())?;
    let source = app
        .path()
        .resolve("examples/climate-trends", BaseDirectory::Resource)
        .map_err(|error| format!("demo resource missing: {error}"))?;
    if !source.is_dir() {
        return Err("demo resource is not bundled in this build".into());
    }
    crate::examples::copy_missing(&source, &root)
        .map_err(|error| format!("demo install failed: {error}"))?;
    let metadata = match std::fs::read(project_file(&root)) {
        Ok(_) => read_or_adopt(&root)?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let timestamp = now();
            let metadata = ProjectMetadata {
                schema_version: PROJECT_SCHEMA_VERSION,
                title: "Spark Agent Climate Trends Demo".into(),
                description: Some("A real bundled climate dataset for General Research.".into()),
                template: Some("demo".into()),
                starter_prompt: Some("Analyze the bundled climate-trends dataset with General Research, save a figure and report, and cite the dataset source.".into()),
                created_at: timestamp.clone(),
                updated_at: timestamp,
                last_session_id: None,
            };
            write_json(&project_file(&root), &metadata)?;
            metadata
        }
        Err(error) => return Err(error.to_string()),
    };
    remember(
        &app,
        &root.canonicalize().map_err(|error| error.to_string())?,
        metadata,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn templates_scaffold_only_general_research_files() {
        let root =
            std::env::temp_dir().join(format!("spark-project-template-{}", std::process::id()));
        let prompt = scaffold(&root, "data-analysis").unwrap();
        assert!(root.join("data/raw").is_dir());
        assert!(root.join("reports").is_dir());
        assert!(root.join(".spark/general-research-starter.md").is_file());
        assert!(prompt.unwrap().contains("General Research"));
        assert!(!root.join("science-core").exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn adoption_writes_schema_version_one_metadata() {
        let root = std::env::temp_dir().join(format!("spark-project-adopt-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let metadata = read_or_adopt(&root).unwrap();
        assert_eq!(metadata.schema_version, 1);
        assert!(project_file(&root).is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn project_titles_cannot_escape_the_selected_parent() {
        assert!(safe_folder_name("../outside").is_err());
        assert!(safe_folder_name("nested/project").is_err());
        assert_eq!(safe_folder_name("my research").unwrap(), "my research");
    }

    #[test]
    fn recent_metadata_is_deduplicated_and_newest_first() {
        let root = PathBuf::from("/tmp/spark-project");
        let metadata = default_metadata(&root);
        let mut recents = Vec::new();
        remember_in(
            &mut recents,
            "/tmp/older".into(),
            metadata.clone(),
            "1".into(),
        );
        remember_in(
            &mut recents,
            "/tmp/spark-project".into(),
            metadata.clone(),
            "2".into(),
        );
        remember_in(&mut recents, "/tmp/older".into(), metadata, "3".into());
        assert_eq!(recents.len(), 2);
        assert_eq!(recents[0].path, "/tmp/older");
        assert_eq!(recents[0].last_opened_at, "3");
    }
}
