/**
 * Internal-MVP runtime policy. Dangerous convenience controls stay compiled
 * out of user-facing flows until they have an isolated execution boundary.
 * Keep these defaults deny-by-default; native enforcement lives in Rust for
 * approval mode, while the frontend store also rejects legacy call paths.
 */
export const RUNTIME_POLICY = Object.freeze({
  allowDirectShell: false,
  allowApprovalModeChanges: false,
  allowPersistentPermissionGrants: false,
  allowCustomMcpServers: false,
} as const);
