# runtime/opencode-profile

The Spark Agent **OpenCode profile** — the config + skills the app ships and applies
to the bundled OpenCode runtime (not a user's global OpenCode).

The desktop app runs OpenCode with an app-private config/data dir (isolated via
`XDG_CONFIG_HOME`/`XDG_DATA_HOME`), so nothing here touches `~/.config/opencode`.

## Contents

```text
opencode.json      # base defaults merged into the app-private runtime config
agents/            # bundled Spark primary agents and sub-agents
skills/            # reserved; bundled skills are deployed from runtime/skills
```

The profile targets the pinned OpenCode **1.17.13**. Its format was verified
against the official `v1.17.13` source at commit
`10c894bdeef3618f5666fb506ef7f9491bb964d8`:

- global and project agents are discovered from `{agent,agents}/**/*.md`;
- global and project skills are discovered from `{skill,skills}/**/SKILL.md`;
- `opencode.json` supports `default_agent`, `agent`, and native permission rules.

Spark uses the plural `agents/` and `skills/` spellings consistently. The base
config is a merge template, not a replacement file: runtime deployment must
preserve provider, model, MCP, credential, and user permission settings, with
explicit user settings taking precedence over bundled defaults.

## Bundled roster

- Primary: `research` (default), `biology`, `physics`, `ml`, `plan`.
- Sub-agents: `literature-review`, `critique`, `reviewer`, `write`, `explore`,
  `task`.

The roster and research-method behaviors are adapted for Spark from OpenScience
at `e9844a49f1f4d93cbf5f88b8f4880c003adc6e61` (Apache-2.0). Prompts are
Spark-specific rewrites: they remove Atlas, wallet, managed-model, mandatory
cloud-GPU, and OpenScience CLI assumptions. See `THIRD_PARTY_NOTICES.md`.

## How it maps at runtime

- A simple provider API key entered in Settings is stored in the OS credential
  manager. The app-private `opencode.json` contains only its environment
  reference, and the sidecar is restarted with the value in its process
  environment. This protects storage at rest, not an approved child tool from
  inheriting the environment.
- Spark-managed Materials Project and FRED connector keys use a separate OS
  credential-manager service. Migration and private-broker infrastructure are
  implemented, and the legacy DYLD-sensitive Spark launcher has been removed.
  Managed entries are disabled by default: credential-bearing execution fails
  closed and Settings marks it security-gated. The staged native path contains
  only Apple platform-signed `/usr/bin/nc -U` to a private Unix-domain socket; the
  Tauri broker binds relay identity to the owned OpenCode PID/start-time/generation
  and validates the strict config and canonical target. It serializes a native
  allow/deny prompt once per broker connection before reading Keychain and revalidates
  that complete scope afterward. This is staged defense,
  not a delivered key-delivery or hard-confinement guarantee. The P0 release gate
  requires immutable signed/verified targets or isolated execution, native
  approval for every credential-using JSON-RPC tool call rather than only every
  connection, and closure of the config-dependency approval bypass. P1
  gates require a fully hashed transitive lock with staged atomic install and
  packaged macOS E2E. Custom/BYO MCP credential custody is not covered.
- Bundled agents are deployed into the app-private profile's global `agents/`
  directory. A workspace's `.opencode/agents/` definitions remain project-owned
  and can override global agents through OpenCode's native precedence.
- Skills are NOT shipped from here: the bundled ai4s-skills pack lives in
  `runtime/skills/external/` (fetched by `scripts/dev/fetch-skills.sh`) and is
  deployed alongside the core Foundation pack by `runtime.rs` into this
  profile's global skills dir
  (`<xdg-config>/opencode/skills/`). They appear on the app's Skills page
  (which lists OpenCode 1.17.13's real `GET /skill?directory=<workspace>`).
- Spark launches OpenCode with an app-private `HOME` and `OPENCODE_PURE=1`.
  Project agents and skills remain discoverable, but external executable plugins
  are not loaded. The pinned sidecar can still perform its config-directory package
  install/write before tool approval, so that separate P0 remains open.

Keep this bundle versioned with the app; it must not carry the user's own keys or sessions.
