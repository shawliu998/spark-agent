# scripts

Repo tooling.

- `release/` — packaging and release scripts (Tauri build matrix, signing/notarization
  helpers, GitHub Release upload, `latest.json` generation).
- `dev/` — local development helpers (bootstrap, run the app, seed the demo workspace).
  `dev/start-internal.sh` is the supported internal-MVP launcher: it checks local
  prerequisites, creates an ephemeral Bearer credential, refuses to share a core
  data directory with another running container, builds and migrates the isolated
  services, discovers the dynamic loopback port, waits for full health, and then
  starts Vite on strict port 5173. Ctrl+C removes only its Compose project and
  transient volumes; the bind-mounted science-core data directory is preserved.
