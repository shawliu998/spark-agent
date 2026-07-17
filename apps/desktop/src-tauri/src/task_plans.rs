// Workspace-local, append-only task-plan journal. The journal is deliberately
// separate from OpenCode sessions: it records Spark's orchestration intent and
// lets a workspace recover its task plans after the desktop app restarts.
use std::collections::HashSet;
use std::fs::OpenOptions;
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use crate::runtime::workspace_dir;

const JOURNAL_DIR: &str = ".spark";
const JOURNAL_FILE: &str = "tasks.jsonl";
const SCHEMA_VERSION: u64 = 1;
const MIN_TASKS: usize = 2;
const MAX_TASKS: usize = 5;

/// Serializes journal reads and appends so records can never interleave.
#[derive(Default)]
pub struct TaskPlanState(pub Mutex<()>);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TaskPlanTask {
    pub id: String,
    pub title: String,
    pub prompt: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TaskSessionRecord {
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requested_model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub route_tier: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub matched_preference: Option<String>,
    pub status: TaskSessionStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    pub recorded_at: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TaskSessionStatus {
    Created,
    Running,
    Completed,
    Failed,
    Canceled,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TaskStartFailure {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    pub error: String,
    pub recorded_at: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct FoldedTaskPlanTask {
    pub id: String,
    pub title: String,
    pub prompt: String,
    #[serde(default)]
    pub sessions: Vec<TaskSessionRecord>,
    #[serde(default)]
    pub start_failures: Vec<TaskStartFailure>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TaskPlan {
    pub schema_version: u64,
    pub plan_id: String,
    pub objective: String,
    pub created_at: u64,
    pub tasks: Vec<FoldedTaskPlanTask>,
    #[serde(default)]
    pub syntheses: Vec<TaskSessionRecord>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(
    tag = "type",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
enum JournalEvent {
    TaskPlanCreated {
        #[serde(rename = "schemaVersion")]
        schema_version: u64,
        recorded_at: u64,
        plan_id: String,
        objective: String,
        tasks: Vec<TaskPlanTask>,
    },
    TaskSessionRecorded {
        #[serde(rename = "schemaVersion")]
        schema_version: u64,
        recorded_at: u64,
        plan_id: String,
        task_id: String,
        session_id: String,
        agent: Option<String>,
        requested_model: Option<String>,
        route_tier: Option<String>,
        matched_preference: Option<String>,
    },
    TaskStartFailed {
        #[serde(rename = "schemaVersion")]
        schema_version: u64,
        recorded_at: u64,
        plan_id: String,
        task_id: String,
        session_id: Option<String>,
        error: String,
    },
    TaskSynthesisRecorded {
        #[serde(rename = "schemaVersion")]
        schema_version: u64,
        recorded_at: u64,
        plan_id: String,
        session_id: String,
        agent: Option<String>,
        requested_model: Option<String>,
        route_tier: Option<String>,
        matched_preference: Option<String>,
    },
    TaskSessionStatusRecorded {
        #[serde(rename = "schemaVersion")]
        schema_version: u64,
        recorded_at: u64,
        plan_id: String,
        session_id: String,
        status: TaskSessionStatus,
        error: Option<String>,
    },
}

fn journal_file(root: &Path) -> PathBuf {
    root.join(JOURNAL_DIR).join(JOURNAL_FILE)
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn validate_required(value: &str, field: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err(format!("{field} must not be empty"));
    }
    if value.chars().any(char::is_control) {
        return Err(format!("{field} must not contain control characters"));
    }
    Ok(())
}

fn validate_prose(value: &str, field: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err(format!("{field} must not be empty"));
    }
    if value.chars().any(|character| {
        character.is_control() && character != '\n' && character != '\t' && character != '\r'
    }) {
        return Err(format!(
            "{field} must not contain unsafe control characters"
        ));
    }
    Ok(())
}

fn validate_identifier(value: &str, field: &str) -> Result<(), String> {
    validate_required(value, field)?;
    if value != value.trim() {
        return Err(format!("{field} must not have surrounding whitespace"));
    }
    Ok(())
}

fn validate_optional(value: Option<&str>, field: &str) -> Result<(), String> {
    if let Some(value) = value {
        validate_required(value, field)?;
    }
    Ok(())
}

fn validate_tasks(tasks: &[TaskPlanTask]) -> Result<(), String> {
    if !(MIN_TASKS..=MAX_TASKS).contains(&tasks.len()) {
        return Err(format!(
            "a task plan must contain between {MIN_TASKS} and {MAX_TASKS} tasks"
        ));
    }
    let mut ids = HashSet::new();
    for task in tasks {
        validate_identifier(&task.id, "task id")?;
        validate_prose(&task.title, "task title")?;
        validate_prose(&task.prompt, "task prompt")?;
        if !ids.insert(&task.id) {
            return Err(format!("duplicate task id: {}", task.id));
        }
    }
    Ok(())
}

fn validate_event_schema(value: &serde_json::Value, line: usize) -> Result<(), String> {
    let schema = value
        .get("schemaVersion")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| format!("task journal line {line} has no valid schemaVersion"))?;
    if schema > SCHEMA_VERSION {
        return Err(format!(
            "task journal line {line} uses future schemaVersion {schema}; upgrade Spark Agent before opening it"
        ));
    }
    if schema != SCHEMA_VERSION {
        return Err(format!(
            "task journal line {line} uses unsupported schemaVersion {schema}"
        ));
    }
    Ok(())
}

fn read_events(path: &Path) -> Result<Vec<JournalEvent>, String> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("could not read task journal: {error}"))?;
    let lines = text.lines().collect::<Vec<_>>();
    let last_non_empty = lines.iter().rposition(|line| !line.trim().is_empty());
    lines
        .into_iter()
        .enumerate()
        .filter(|(_, line)| !line.trim().is_empty())
        .map(|(index, line)| {
            let line_number = index + 1;
            let value: serde_json::Value = match serde_json::from_str(line) {
                Ok(value) => value,
                Err(_) if Some(index) == last_non_empty && !text.ends_with('\n') => {
                    return Ok(None)
                }
                Err(error) => {
                    return Err(format!("invalid task journal line {line_number}: {error}"))
                }
            };
            validate_event_schema(&value, line_number)?;
            serde_json::from_value(value)
                .map(Some)
                .map_err(|error| format!("invalid task journal line {line_number}: {error}"))
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|events| events.into_iter().flatten().collect())
}

fn fold_events(events: &[JournalEvent]) -> Result<Vec<TaskPlan>, String> {
    let mut plans = Vec::<TaskPlan>::new();
    for event in events {
        match event {
            JournalEvent::TaskPlanCreated {
                schema_version,
                recorded_at,
                plan_id,
                objective,
                tasks,
            } => {
                if *schema_version != SCHEMA_VERSION {
                    return Err("unsupported task journal schemaVersion".to_string());
                }
                validate_identifier(plan_id, "plan id")?;
                validate_prose(objective, "objective")?;
                validate_tasks(tasks)?;
                if plans.iter().any(|plan| plan.plan_id == *plan_id) {
                    return Err(format!("duplicate task plan id: {plan_id}"));
                }
                plans.push(TaskPlan {
                    schema_version: SCHEMA_VERSION,
                    plan_id: plan_id.clone(),
                    objective: objective.clone(),
                    created_at: *recorded_at,
                    tasks: tasks
                        .iter()
                        .map(|task| FoldedTaskPlanTask {
                            id: task.id.clone(),
                            title: task.title.clone(),
                            prompt: task.prompt.clone(),
                            sessions: Vec::new(),
                            start_failures: Vec::new(),
                        })
                        .collect(),
                    syntheses: Vec::new(),
                });
            }
            JournalEvent::TaskSessionRecorded {
                schema_version,
                recorded_at,
                plan_id,
                task_id,
                session_id,
                agent,
                requested_model,
                route_tier,
                matched_preference,
            } => {
                if *schema_version != SCHEMA_VERSION {
                    return Err("unsupported task journal schemaVersion".to_string());
                }
                validate_identifier(plan_id, "plan id")?;
                validate_identifier(task_id, "task id")?;
                validate_identifier(session_id, "session id")?;
                let plan = plans
                    .iter_mut()
                    .find(|plan| plan.plan_id == *plan_id)
                    .ok_or_else(|| format!("task session references unknown plan: {plan_id}"))?;
                if plan
                    .tasks
                    .iter()
                    .flat_map(|task| task.sessions.iter())
                    .any(|session| session.session_id == *session_id)
                {
                    return Err(format!("duplicate task session id: {session_id}"));
                }
                let task = plan
                    .tasks
                    .iter_mut()
                    .find(|task| task.id == *task_id)
                    .ok_or_else(|| format!("task session references unknown task: {task_id}"))?;
                validate_optional(agent.as_deref(), "agent")?;
                validate_optional(requested_model.as_deref(), "requested model")?;
                validate_optional(route_tier.as_deref(), "route tier")?;
                validate_optional(matched_preference.as_deref(), "matched preference")?;
                task.sessions.push(TaskSessionRecord {
                    session_id: session_id.clone(),
                    agent: agent.clone(),
                    requested_model: requested_model.clone(),
                    route_tier: route_tier.clone(),
                    matched_preference: matched_preference.clone(),
                    status: TaskSessionStatus::Created,
                    error: None,
                    recorded_at: *recorded_at,
                });
            }
            JournalEvent::TaskStartFailed {
                schema_version,
                recorded_at,
                plan_id,
                task_id,
                session_id,
                error,
            } => {
                if *schema_version != SCHEMA_VERSION {
                    return Err("unsupported task journal schemaVersion".to_string());
                }
                validate_identifier(plan_id, "plan id")?;
                validate_identifier(task_id, "task id")?;
                if let Some(session_id) = session_id {
                    validate_identifier(session_id, "session id")?;
                }
                validate_prose(error, "task start error")?;
                let plan = plans
                    .iter_mut()
                    .find(|plan| plan.plan_id == *plan_id)
                    .ok_or_else(|| format!("task failure references unknown plan: {plan_id}"))?;
                let task = plan
                    .tasks
                    .iter_mut()
                    .find(|task| task.id == *task_id)
                    .ok_or_else(|| format!("task failure references unknown task: {task_id}"))?;
                task.start_failures.push(TaskStartFailure {
                    session_id: session_id.clone(),
                    error: error.clone(),
                    recorded_at: *recorded_at,
                });
            }
            JournalEvent::TaskSynthesisRecorded {
                schema_version,
                recorded_at,
                plan_id,
                session_id,
                agent,
                requested_model,
                route_tier,
                matched_preference,
            } => {
                if *schema_version != SCHEMA_VERSION {
                    return Err("unsupported task journal schemaVersion".to_string());
                }
                validate_identifier(plan_id, "plan id")?;
                validate_identifier(session_id, "session id")?;
                validate_optional(agent.as_deref(), "agent")?;
                validate_optional(requested_model.as_deref(), "requested model")?;
                validate_optional(route_tier.as_deref(), "route tier")?;
                validate_optional(matched_preference.as_deref(), "matched preference")?;
                let plan = plans
                    .iter_mut()
                    .find(|plan| plan.plan_id == *plan_id)
                    .ok_or_else(|| format!("task synthesis references unknown plan: {plan_id}"))?;
                let duplicate = plan
                    .syntheses
                    .iter()
                    .any(|item| item.session_id == *session_id)
                    || plan
                        .tasks
                        .iter()
                        .flat_map(|task| task.sessions.iter())
                        .any(|item| item.session_id == *session_id);
                if duplicate {
                    return Err(format!("duplicate task session id: {session_id}"));
                }
                plan.syntheses.push(TaskSessionRecord {
                    session_id: session_id.clone(),
                    agent: agent.clone(),
                    requested_model: requested_model.clone(),
                    route_tier: route_tier.clone(),
                    matched_preference: matched_preference.clone(),
                    status: TaskSessionStatus::Created,
                    error: None,
                    recorded_at: *recorded_at,
                });
            }
            JournalEvent::TaskSessionStatusRecorded {
                schema_version,
                recorded_at: _,
                plan_id,
                session_id,
                status,
                error,
            } => {
                if *schema_version != SCHEMA_VERSION {
                    return Err("unsupported task journal schemaVersion".to_string());
                }
                validate_identifier(plan_id, "plan id")?;
                validate_identifier(session_id, "session id")?;
                if let Some(error) = error.as_deref() {
                    validate_prose(error, "task session error")?;
                }
                if *status == TaskSessionStatus::Created {
                    return Err("task status events cannot record created".to_string());
                }
                let plan = plans
                    .iter_mut()
                    .find(|plan| plan.plan_id == *plan_id)
                    .ok_or_else(|| format!("task status references unknown plan: {plan_id}"))?;
                let session = plan
                    .tasks
                    .iter_mut()
                    .flat_map(|task| task.sessions.iter_mut())
                    .chain(plan.syntheses.iter_mut())
                    .find(|session| session.session_id == *session_id)
                    .ok_or_else(|| {
                        format!("task status references unknown session: {session_id}")
                    })?;
                let terminal = matches!(
                    session.status,
                    TaskSessionStatus::Completed
                        | TaskSessionStatus::Failed
                        | TaskSessionStatus::Canceled
                );
                let terminal_override = session.status == TaskSessionStatus::Completed
                    && matches!(
                        status,
                        TaskSessionStatus::Failed | TaskSessionStatus::Canceled
                    );
                if !terminal || terminal_override {
                    session.status = status.clone();
                    session.error = error.clone();
                }
            }
        }
    }
    Ok(plans)
}

fn read_task_plans(root: &Path) -> Result<Vec<TaskPlan>, String> {
    fold_events(&read_events(&journal_file(root))?)
}

fn repair_trailing_record(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    let bytes =
        std::fs::read(path).map_err(|error| format!("could not inspect task journal: {error}"))?;
    if bytes.is_empty() || bytes.ends_with(b"\n") {
        return Ok(());
    }
    let tail_start = bytes
        .iter()
        .rposition(|byte| *byte == b'\n')
        .map_or(0, |index| index + 1);
    let tail_is_valid = serde_json::from_slice::<serde_json::Value>(&bytes[tail_start..]).is_ok();
    let mut file = OpenOptions::new()
        .write(true)
        .open(path)
        .map_err(|error| format!("could not repair task journal: {error}"))?;
    if tail_is_valid {
        file.seek(SeekFrom::End(0))
            .map_err(|error| error.to_string())?;
        file.write_all(b"\n")
            .map_err(|error| format!("could not repair task journal: {error}"))?;
    } else {
        file.set_len(tail_start as u64)
            .map_err(|error| format!("could not truncate task journal tail: {error}"))?;
    }
    file.sync_data()
        .map_err(|error| format!("could not sync repaired task journal: {error}"))
}

fn append_event(root: &Path, event: &JournalEvent) -> Result<(), String> {
    let path = journal_file(root);
    let parent = path
        .parent()
        .ok_or_else(|| "invalid task journal path".to_string())?;
    std::fs::create_dir_all(parent)
        .map_err(|error| format!("could not create task journal directory: {error}"))?;
    repair_trailing_record(&path)?;
    let json = serde_json::to_string(event).map_err(|error| error.to_string())?;
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| format!("could not open task journal: {error}"))?;
    file.write_all(json.as_bytes())
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_data())
        .map_err(|error| format!("could not append task journal: {error}"))
}

#[tauri::command(async)]
pub fn record_task_session_status(
    app: AppHandle,
    state: tauri::State<TaskPlanState>,
    plan_id: String,
    session_id: String,
    status: TaskSessionStatus,
    error: Option<String>,
) -> Result<TaskPlan, String> {
    validate_identifier(&plan_id, "plan id")?;
    validate_identifier(&session_id, "session id")?;
    if let Some(error) = error.as_deref() {
        validate_prose(error, "task session error")?;
    }
    if status == TaskSessionStatus::Created {
        return Err("task status events cannot record created".to_string());
    }
    let _guard = state.0.lock().map_err(|_| "task journal lock poisoned")?;
    let root = workspace_dir(&app)?;
    let plans = read_task_plans(&root)?;
    let plan = plans
        .iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| format!("unknown task plan: {plan_id}"))?;
    let known = plan
        .tasks
        .iter()
        .flat_map(|task| task.sessions.iter())
        .chain(plan.syntheses.iter())
        .any(|session| session.session_id == session_id);
    if !known {
        return Err(format!("unknown task session: {session_id}"));
    }
    append_event(
        &root,
        &JournalEvent::TaskSessionStatusRecorded {
            schema_version: SCHEMA_VERSION,
            recorded_at: now_ms(),
            plan_id: plan_id.clone(),
            session_id,
            status,
            error,
        },
    )?;
    read_task_plans(&root)?
        .into_iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| "recorded task plan was not found in its journal".to_string())
}

#[tauri::command(async)]
pub fn create_task_plan(
    app: AppHandle,
    state: tauri::State<TaskPlanState>,
    plan_id: String,
    objective: String,
    tasks: Vec<TaskPlanTask>,
) -> Result<TaskPlan, String> {
    validate_identifier(&plan_id, "plan id")?;
    validate_prose(&objective, "objective")?;
    validate_tasks(&tasks)?;
    let _guard = state.0.lock().map_err(|_| "task journal lock poisoned")?;
    let root = workspace_dir(&app)?;
    if read_task_plans(&root)?
        .iter()
        .any(|plan| plan.plan_id == plan_id)
    {
        return Err(format!("duplicate task plan id: {plan_id}"));
    }
    let recorded_at = now_ms();
    append_event(
        &root,
        &JournalEvent::TaskPlanCreated {
            schema_version: SCHEMA_VERSION,
            recorded_at,
            plan_id: plan_id.clone(),
            objective: objective.clone(),
            tasks,
        },
    )?;
    read_task_plans(&root)?
        .into_iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| "created task plan was not found in its journal".to_string())
}

#[tauri::command(async)]
#[allow(clippy::too_many_arguments)]
pub fn record_task_session(
    app: AppHandle,
    state: tauri::State<TaskPlanState>,
    plan_id: String,
    task_id: String,
    session_id: String,
    agent: Option<String>,
    requested_model: Option<String>,
    route_tier: Option<String>,
    matched_preference: Option<String>,
) -> Result<TaskPlan, String> {
    validate_identifier(&plan_id, "plan id")?;
    validate_identifier(&task_id, "task id")?;
    validate_identifier(&session_id, "session id")?;
    validate_optional(agent.as_deref(), "agent")?;
    validate_optional(requested_model.as_deref(), "requested model")?;
    validate_optional(route_tier.as_deref(), "route tier")?;
    validate_optional(matched_preference.as_deref(), "matched preference")?;
    let _guard = state.0.lock().map_err(|_| "task journal lock poisoned")?;
    let root = workspace_dir(&app)?;
    let plans = read_task_plans(&root)?;
    let plan = plans
        .iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| format!("unknown task plan: {plan_id}"))?;
    if !plan.tasks.iter().any(|task| task.id == task_id) {
        return Err(format!("unknown task id: {task_id}"));
    }
    if plan
        .tasks
        .iter()
        .flat_map(|task| task.sessions.iter())
        .any(|session| session.session_id == session_id)
    {
        return Err(format!("duplicate task session id: {session_id}"));
    }
    append_event(
        &root,
        &JournalEvent::TaskSessionRecorded {
            schema_version: SCHEMA_VERSION,
            recorded_at: now_ms(),
            plan_id: plan_id.clone(),
            task_id,
            session_id,
            agent,
            requested_model,
            route_tier,
            matched_preference,
        },
    )?;
    read_task_plans(&root)?
        .into_iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| "recorded task plan was not found in its journal".to_string())
}

#[tauri::command(async)]
pub fn record_task_start_failure(
    app: AppHandle,
    state: tauri::State<TaskPlanState>,
    plan_id: String,
    task_id: String,
    session_id: Option<String>,
    error: String,
) -> Result<TaskPlan, String> {
    validate_identifier(&plan_id, "plan id")?;
    validate_identifier(&task_id, "task id")?;
    if let Some(session_id) = session_id.as_deref() {
        validate_identifier(session_id, "session id")?;
    }
    validate_prose(&error, "task start error")?;
    let _guard = state.0.lock().map_err(|_| "task journal lock poisoned")?;
    let root = workspace_dir(&app)?;
    let plans = read_task_plans(&root)?;
    let plan = plans
        .iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| format!("unknown task plan: {plan_id}"))?;
    if !plan.tasks.iter().any(|task| task.id == task_id) {
        return Err(format!("unknown task id: {task_id}"));
    }
    append_event(
        &root,
        &JournalEvent::TaskStartFailed {
            schema_version: SCHEMA_VERSION,
            recorded_at: now_ms(),
            plan_id: plan_id.clone(),
            task_id,
            session_id,
            error,
        },
    )?;
    read_task_plans(&root)?
        .into_iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| "recorded task plan was not found in its journal".to_string())
}

#[tauri::command(async)]
#[allow(clippy::too_many_arguments)]
pub fn record_task_synthesis(
    app: AppHandle,
    state: tauri::State<TaskPlanState>,
    plan_id: String,
    session_id: String,
    agent: Option<String>,
    requested_model: Option<String>,
    route_tier: Option<String>,
    matched_preference: Option<String>,
) -> Result<TaskPlan, String> {
    validate_identifier(&plan_id, "plan id")?;
    validate_identifier(&session_id, "session id")?;
    validate_optional(agent.as_deref(), "agent")?;
    validate_optional(requested_model.as_deref(), "requested model")?;
    validate_optional(route_tier.as_deref(), "route tier")?;
    validate_optional(matched_preference.as_deref(), "matched preference")?;
    let _guard = state.0.lock().map_err(|_| "task journal lock poisoned")?;
    let root = workspace_dir(&app)?;
    let plans = read_task_plans(&root)?;
    let plan = plans
        .iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| format!("unknown task plan: {plan_id}"))?;
    let duplicate = plan
        .syntheses
        .iter()
        .any(|item| item.session_id == session_id)
        || plan
            .tasks
            .iter()
            .flat_map(|task| task.sessions.iter())
            .any(|item| item.session_id == session_id);
    if duplicate {
        return Err(format!("duplicate task session id: {session_id}"));
    }
    append_event(
        &root,
        &JournalEvent::TaskSynthesisRecorded {
            schema_version: SCHEMA_VERSION,
            recorded_at: now_ms(),
            plan_id: plan_id.clone(),
            session_id,
            agent,
            requested_model,
            route_tier,
            matched_preference,
        },
    )?;
    read_task_plans(&root)?
        .into_iter()
        .find(|plan| plan.plan_id == plan_id)
        .ok_or_else(|| "recorded task plan was not found in its journal".to_string())
}

#[tauri::command(async)]
pub fn list_task_plans(
    app: AppHandle,
    state: tauri::State<TaskPlanState>,
) -> Result<Vec<TaskPlan>, String> {
    let _guard = state.0.lock().map_err(|_| "task journal lock poisoned")?;
    read_task_plans(&workspace_dir(&app)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(tag: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "spark-task-plans-{tag}-{}-{}",
            std::process::id(),
            now_ms()
        ));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    fn tasks() -> Vec<TaskPlanTask> {
        vec![
            TaskPlanTask {
                id: "evidence".into(),
                title: "Gather evidence".into(),
                prompt: "Find evidence.".into(),
            },
            TaskPlanTask {
                id: "review".into(),
                title: "Review evidence".into(),
                prompt: "Challenge it.".into(),
            },
        ]
    }

    #[test]
    fn journal_round_trip_folds_plan_session_and_failure() {
        let root = temp_root("roundtrip");
        append_event(
            &root,
            &JournalEvent::TaskPlanCreated {
                schema_version: SCHEMA_VERSION,
                recorded_at: 10,
                plan_id: "plan-1".into(),
                objective: "Assess the result".into(),
                tasks: tasks(),
            },
        )
        .unwrap();
        append_event(
            &root,
            &JournalEvent::TaskSessionRecorded {
                schema_version: SCHEMA_VERSION,
                recorded_at: 11,
                plan_id: "plan-1".into(),
                task_id: "evidence".into(),
                session_id: "session-1".into(),
                agent: Some("research".into()),
                requested_model: Some("provider/terra".into()),
                route_tier: Some("standard".into()),
                matched_preference: Some("terra".into()),
            },
        )
        .unwrap();
        append_event(
            &root,
            &JournalEvent::TaskStartFailed {
                schema_version: SCHEMA_VERSION,
                recorded_at: 12,
                plan_id: "plan-1".into(),
                task_id: "review".into(),
                session_id: None,
                error: "runtime disconnected".into(),
            },
        )
        .unwrap();
        append_event(
            &root,
            &JournalEvent::TaskSynthesisRecorded {
                schema_version: SCHEMA_VERSION,
                recorded_at: 13,
                plan_id: "plan-1".into(),
                session_id: "session-synthesis".into(),
                agent: Some("research".into()),
                requested_model: Some("provider/sol".into()),
                route_tier: Some("deep".into()),
                matched_preference: Some("sol".into()),
            },
        )
        .unwrap();
        append_event(
            &root,
            &JournalEvent::TaskSessionStatusRecorded {
                schema_version: SCHEMA_VERSION,
                recorded_at: 14,
                plan_id: "plan-1".into(),
                session_id: "session-synthesis".into(),
                status: TaskSessionStatus::Completed,
                error: None,
            },
        )
        .unwrap();
        append_event(
            &root,
            &JournalEvent::TaskSessionStatusRecorded {
                schema_version: SCHEMA_VERSION,
                recorded_at: 16,
                plan_id: "plan-1".into(),
                session_id: "session-synthesis".into(),
                status: TaskSessionStatus::Failed,
                error: Some("provider failed".into()),
            },
        )
        .unwrap();
        append_event(
            &root,
            &JournalEvent::TaskSessionStatusRecorded {
                schema_version: SCHEMA_VERSION,
                recorded_at: 17,
                plan_id: "plan-1".into(),
                session_id: "session-synthesis".into(),
                status: TaskSessionStatus::Completed,
                error: None,
            },
        )
        .unwrap();
        append_event(
            &root,
            &JournalEvent::TaskSessionStatusRecorded {
                schema_version: SCHEMA_VERSION,
                recorded_at: 15,
                plan_id: "plan-1".into(),
                session_id: "session-synthesis".into(),
                status: TaskSessionStatus::Running,
                error: None,
            },
        )
        .unwrap();

        let plans = read_task_plans(&root).unwrap();
        assert_eq!(plans.len(), 1);
        assert_eq!(plans[0].tasks[0].sessions[0].session_id, "session-1");
        assert_eq!(
            plans[0].tasks[1].start_failures[0].error,
            "runtime disconnected"
        );
        assert_eq!(plans[0].syntheses[0].session_id, "session-synthesis");
        assert_eq!(plans[0].syntheses[0].status, TaskSessionStatus::Failed);
        assert_eq!(
            plans[0].syntheses[0].error.as_deref(),
            Some("provider failed")
        );
        let text = std::fs::read_to_string(journal_file(&root)).unwrap();
        assert!(text
            .lines()
            .all(|line| line.contains("\"schemaVersion\":1")));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn rejects_invalid_and_duplicate_task_input() {
        let invalid = vec![TaskPlanTask {
            id: " ".into(),
            title: "".into(),
            prompt: " ".into(),
        }];
        assert!(validate_tasks(&invalid).is_err());
        let duplicate = vec![tasks()[0].clone(), tasks()[0].clone()];
        assert!(validate_tasks(&duplicate)
            .unwrap_err()
            .contains("duplicate"));
    }

    #[test]
    fn rejects_future_schema_without_silently_reading_it() {
        let root = temp_root("future");
        let path = journal_file(&root);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(
            &path,
            r#"{"schemaVersion":2,"type":"task_plan_created","recorded_at":1,"plan_id":"plan-1","objective":"x","tasks":[]}"#,
        )
        .unwrap();
        assert!(read_task_plans(&root)
            .unwrap_err()
            .contains("future schemaVersion 2"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn accepts_multiline_prose_but_rejects_unsafe_controls() {
        let mut multiline = tasks();
        multiline[0].prompt = "First line\nSecond line\twith detail".into();
        assert!(validate_tasks(&multiline).is_ok());
        multiline[0].prompt = "unsafe\u{0000}text".into();
        assert!(validate_tasks(&multiline).is_err());
    }

    #[test]
    fn repairs_only_a_truncated_trailing_record_before_append() {
        let root = temp_root("trailing-record");
        append_event(
            &root,
            &JournalEvent::TaskPlanCreated {
                schema_version: SCHEMA_VERSION,
                recorded_at: 10,
                plan_id: "plan-1".into(),
                objective: "Assess\nthis result".into(),
                tasks: tasks(),
            },
        )
        .unwrap();
        let path = journal_file(&root);
        let mut file = OpenOptions::new().append(true).open(&path).unwrap();
        file.write_all(br#"{"schemaVersion":1,"type":"task_sess"#)
            .unwrap();
        drop(file);
        assert_eq!(read_task_plans(&root).unwrap().len(), 1);
        append_event(
            &root,
            &JournalEvent::TaskStartFailed {
                schema_version: SCHEMA_VERSION,
                recorded_at: 11,
                plan_id: "plan-1".into(),
                task_id: "review".into(),
                session_id: None,
                error: "retry needed".into(),
            },
        )
        .unwrap();
        let plans = read_task_plans(&root).unwrap();
        assert_eq!(plans[0].tasks[1].start_failures.len(), 1);
        let _ = std::fs::remove_dir_all(root);
    }
}
