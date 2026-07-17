// Folder-backed Spark projects. The project file deliberately contains only
// portable project metadata; OpenCode remains the owner of conversations.
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::{path::BaseDirectory, AppHandle, Manager};

const PROJECT_SCHEMA_VERSION: u32 = 1;
const PROJECT_FILE: &str = "project.json";
const RECENTS_FILE: &str = "recent-projects.json";
const DEMO_RESOURCE: &str = "examples/research-demo";
const DEMO_FOLDER: &str = "spark-agent-research-demo";
static NEXT_PROJECT_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMetadata {
    pub schema_version: u32,
    #[serde(default)]
    pub id: String,
    pub title: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub template: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub starter_prompt: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default)]
    pub workspace_path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_session_id: Option<String>,
}

fn new_project_id() -> String {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let sequence = NEXT_PROJECT_ID.fetch_add(1, Ordering::Relaxed);
    format!("spark-{timestamp:x}-{sequence:x}")
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
        "dataset-analysis" => Ok(Some((
            "Dataset analysis",
            "Start a General Research data analysis. Inspect the project data, document data quality and assumptions, write and run the analysis, save figures and tables, then report results and limitations.",
        ))),
        "papers-and-data" => Ok(Some((
            "Papers and data",
            "Start a General Research project that connects the project papers with its datasets. Inventory local papers and data first; when useful, use configured credential-free literature search to fill evidence gaps, but treat local papers as sufficient when they answer the question. Resolve stable identifiers, inspect data quality, and write and execute reproducible analysis code. Produce references/corpus.csv, references/references.bib, scripts/papers_data_analysis.py, tables/papers_data_summary.csv, figures/papers_data_analysis.png, and reports/papers-data-synthesis.md. Trace every numeric claim to executed output and every literature claim to a listed source, and state material limitations.",
        ))),
        "reproduce-result" => Ok(Some((
            "Reproduce a result",
            "Start a General Research reproduction. Identify the target result and available evidence, implement the method, run validation checks, compare the outcome, and document deviations and limitations.",
        ))),
        "research-report" => Ok(Some((
            "Research report",
            "Start a General Research report. Inspect the project evidence and artifacts, identify remaining gaps, write a structured report, and ask the reviewer agent to check claims, figures, and references.",
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
        id: new_project_id(),
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
        workspace_path: root.to_string_lossy().to_string(),
        last_session_id: None,
    }
}

fn read_or_adopt(root: &Path) -> Result<ProjectMetadata, String> {
    let file = project_file(root);
    match std::fs::read(&file) {
        Ok(contents) => {
            let mut metadata: ProjectMetadata = serde_json::from_slice(&contents)
                .map_err(|error| format!("invalid .spark/project.json: {error}"))?;
            if metadata.schema_version != PROJECT_SCHEMA_VERSION {
                return Err(format!(
                    "unsupported project schema version: {}",
                    metadata.schema_version
                ));
            }
            let workspace_path = root.to_string_lossy().to_string();
            let mut migrated = false;
            if metadata.id.is_empty() {
                metadata.id = new_project_id();
                migrated = true;
            }
            if metadata.workspace_path != workspace_path {
                metadata.workspace_path = workspace_path;
                migrated = true;
            }
            if migrated {
                metadata.updated_at = now();
                write_json(&file, &metadata)?;
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
    match std::fs::read(file) {
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
        let canonical_root = root.canonicalize().map_err(|error| error.to_string())?;
        let timestamp = now();
        let metadata = ProjectMetadata {
            schema_version: PROJECT_SCHEMA_VERSION,
            id: new_project_id(),
            title: title.trim().to_string(),
            description: None,
            template: (template != "blank").then_some(template),
            starter_prompt,
            created_at: timestamp.clone(),
            updated_at: timestamp,
            workspace_path: canonical_root.to_string_lossy().to_string(),
            last_session_id: None,
        };
        write_json(&project_file(&root), &metadata)?;
        remember(&app, &canonical_root, metadata)
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
            last_opened_at: entry.last_opened_at.clone(),
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
    // Keep these constants isolated: the independently maintained demo can be
    // retargeted without changing project metadata or activation behavior.
    let root = crate::runtime::base_workspace_dir(&app)?.join(DEMO_FOLDER);
    std::fs::create_dir_all(&root).map_err(|error| error.to_string())?;
    let source = app
        .path()
        .resolve(DEMO_RESOURCE, BaseDirectory::Resource)
        .map_err(|error| format!("demo resource missing: {error}"))?;
    if !source.is_dir() {
        return Err("demo resource is not bundled in this build".into());
    }
    crate::examples::copy_missing(&source, &root)
        .map_err(|error| format!("demo install failed: {error}"))?;
    let canonical_root = root.canonicalize().map_err(|error| error.to_string())?;
    let metadata = match std::fs::read(project_file(&root)) {
        Ok(_) => read_or_adopt(&root)?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let timestamp = now();
            let metadata = ProjectMetadata {
                schema_version: PROJECT_SCHEMA_VERSION,
                id: new_project_id(),
                title: "Synthetic Research Demo".into(),
                description: Some("A deterministic synthetic project for learning the General Research workflow.".into()),
                template: Some("demo".into()),
                starter_prompt: Some("Reproduce the bundled synthetic analysis with General Research, inspect every generated artifact, and explain the evidence and limitations without making real scientific claims.".into()),
                created_at: timestamp.clone(),
                updated_at: timestamp,
                workspace_path: canonical_root.to_string_lossy().to_string(),
                last_session_id: None,
            };
            write_json(&project_file(&root), &metadata)?;
            metadata
        }
        Err(error) => return Err(error.to_string()),
    };
    remember(&app, &canonical_root, metadata)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn templates_scaffold_only_general_research_files() {
        let root =
            std::env::temp_dir().join(format!("spark-project-template-{}", std::process::id()));
        let prompt = scaffold(&root, "dataset-analysis").unwrap();
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
        assert!(!metadata.id.is_empty());
        assert_eq!(metadata.workspace_path, root.to_string_lossy().to_string());
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

    #[test]
    fn exact_project_template_catalog_excludes_verified_workflows() {
        for template in [
            "blank",
            "literature-review",
            "dataset-analysis",
            "papers-and-data",
            "reproduce-result",
            "research-report",
        ] {
            assert!(
                template_prompt(template).is_ok(),
                "missing template: {template}"
            );
        }
        assert!(template_prompt("computational-study").is_err());
        assert!(template_prompt("verified-dataset-analysis").is_err());
    }

    #[test]
    fn papers_and_data_template_names_the_reproducible_deliverables() {
        let (_, prompt) = template_prompt("papers-and-data").unwrap().unwrap();
        for path in [
            "references/corpus.csv",
            "references/references.bib",
            "scripts/papers_data_analysis.py",
            "tables/papers_data_summary.csv",
            "figures/papers_data_analysis.png",
            "reports/papers-data-synthesis.md",
        ] {
            assert!(prompt.contains(path), "missing deliverable {path}");
        }
        assert!(prompt.contains("stable identifiers"));
        assert!(prompt.contains("state material limitations"));
    }
}
