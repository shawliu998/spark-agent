/**
 * Runtime capability policy. Keep controls that broaden the workspace or
 * persist trust compiled out until they have an isolated execution boundary.
 * Approval mode is enforced and persisted by the native layer. The Composer
 * can restore manual approval but never offers legacy Full Access.
 */
export const RUNTIME_POLICY = Object.freeze({
  allowDirectShell: false,
  allowApprovalModeChanges: true,
  allowPersistentPermissionGrants: false,
  allowCustomMcpServers: false,
} as const);
