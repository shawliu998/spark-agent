// Persisted task-plan contract. The Rust store folds append-only plan, session,
// and start-failure events into this shape when it is read back.
import { isTauri } from "./tauri";

export const TASK_PLAN_SCHEMA_VERSION = 1 as const;

export type TaskPlanRouteTier = "quick" | "standard" | "deep";
export type TaskPlanSessionStatus = "created" | "running" | "completed" | "failed" | "canceled";

export interface TaskPlanTaskInput {
  id: string;
  title: string;
  prompt: string;
}

export interface CreateTaskPlanInput {
  planId: string;
  objective: string;
  tasks: TaskPlanTaskInput[];
}

/** A session that was requested for a task. `requestedModel` is a request,
 * not a claim that the provider actually used that model. */
export interface TaskPlanSessionRecord {
  sessionId: string;
  agent: string | null;
  requestedModel: string | null;
  routeTier: TaskPlanRouteTier | null;
  matchedPreference: string | null;
  status: TaskPlanSessionStatus;
  error: string | null;
  recordedAt: number;
}

/** Failure to create or start a task session. Later retries remain separate
 * records so the plan's history does not lose failed attempts. */
export interface TaskPlanStartFailureRecord {
  sessionId: string | null;
  error: string;
  recordedAt: number;
}

/** One task in a folded task-plan record. */
export interface TaskPlanTaskRecord extends TaskPlanTaskInput {
  sessions: TaskPlanSessionRecord[];
  startFailures: TaskPlanStartFailureRecord[];
}

/** Schema-v1 durable view returned by the native task-plan journal. */
export interface TaskPlanRecord {
  schemaVersion: typeof TASK_PLAN_SCHEMA_VERSION;
  planId: string;
  objective: string;
  createdAt: number;
  tasks: TaskPlanTaskRecord[];
  syntheses: TaskPlanSessionRecord[];
}

export interface RecordTaskSessionInput
  extends Omit<TaskPlanSessionRecord, "recordedAt" | "status" | "error"> {
  planId: string;
  taskId: string;
}

export interface RecordTaskSessionStatusInput {
  planId: string;
  sessionId: string;
  status: Exclude<TaskPlanSessionStatus, "created">;
  error?: string | null;
}

export interface RecordTaskStartFailureInput {
  planId: string;
  taskId: string;
  sessionId?: string | null;
  error: string;
}

export interface RecordTaskSynthesisInput
  extends Omit<TaskPlanSessionRecord, "recordedAt" | "status" | "error"> {
  planId: string;
}

/** Create the durable parent record before any child session is started. */
export async function createTaskPlanRecord(input: CreateTaskPlanInput): Promise<void> {
  if (!isTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("create_task_plan", { ...input });
}

/** Append a successful task-session start to its plan. */
export async function recordTaskSession(input: RecordTaskSessionInput): Promise<void> {
  if (!isTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("record_task_session", { ...input });
}

/** Advance a task or synthesis attempt. Native folding makes terminal states
 * absorbing so an idle event that beats the prompt response cannot regress. */
export async function recordTaskSessionStatus(input: RecordTaskSessionStatusInput): Promise<void> {
  if (!isTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("record_task_session_status", { ...input });
}

/** Append a task-session startup failure without discarding other tasks. */
export async function recordTaskStartFailure(input: RecordTaskStartFailureInput): Promise<void> {
  if (!isTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("record_task_start_failure", { ...input });
}

/** Associate a synthesis session with its parent plan without pretending it is
 * one of the independently planned tasks. */
export async function recordTaskSynthesis(input: RecordTaskSynthesisInput): Promise<void> {
  if (!isTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("record_task_synthesis", { ...input });
}

/** Read folded task plans. Browser development has no local native journal. */
export async function listTaskPlans(): Promise<TaskPlanRecord[]> {
  if (!isTauri) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<TaskPlanRecord[]>("list_task_plans");
}
