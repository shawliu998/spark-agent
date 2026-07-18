/**
 * Runtime capability policy. Keep controls that broaden the workspace or
 * persist trust compiled out until they have an isolated execution boundary.
 * Permission presets are enforced and persisted by the native layer.
 */
export const RUNTIME_POLICY = Object.freeze({
  allowDirectShell: false,
  allowApprovalModeChanges: true,
  allowPersistentPermissionGrants: false,
  allowCustomMcpServers: false,
} as const);
