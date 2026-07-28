// Pure merge of provider credentials/model into OpenCode config JSON.
// Used by the runtime command, which writes it into an app-private config dir.
use serde_json::{json, Value};

/// The internal MVP has one approval mode: safe, manual approval. `MODE_FULL`
/// is retained only to recognize and repair config written by older builds.
pub const MODE_APPROVE: &str = "approve";
pub const MODE_FULL: &str = "full";

fn approve_permission() -> Value {
    // OpenCode's builtin default allows most tools. Make unknown tools (which
    // includes MCP tools) ask, permit only read-only local operations, ask for
    // all writes/commands/network calls, and deny paths outside the workspace.
    json!({
        "*": "ask",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "task": "allow",
        "skill": "allow",
        "lsp": "allow",
        "edit": "ask",
        "bash": "ask",
        "webfetch": "ask",
        "websearch": "ask",
        // Paper discovery must go through the future Science Core broker, where
        // an approved DiscoverySpec and per-run remote-data approval are bound
        // before send.  Do not let ambient OpenCode permissions bypass that
        // control plane; `read_*` may also resolve or write remote content.
        "paper-search_search_*": "deny",
        "paper-search_read_*": "deny",
        "paper-search_download_*": "deny",
        "external_directory": "deny"
    })
}

/// Install the safe permission policy. Full access is deliberately rejected;
/// other config keys are preserved.
pub fn set_permission_mode(existing: &str, mode: &str) -> Result<String, String> {
    let permission = match mode {
        MODE_APPROVE => approve_permission(),
        MODE_FULL => return Err("full access is disabled by the internal safety policy".into()),
        other => return Err(format!("unknown approval mode \"{other}\"")),
    };
    let mut root: Value = if existing.trim().is_empty() {
        json!({})
    } else {
        serde_json::from_str(existing).map_err(|e| format!("invalid existing config: {e}"))?
    };
    if !root.is_object() {
        root = json!({});
    }
    root.as_object_mut()
        .unwrap()
        .insert("permission".to_string(), permission);
    serde_json::to_string_pretty(&root).map_err(|e| e.to_string())
}

/// Enforce the safe policy on every runtime start. This also repairs legacy
/// full-access and partial-approval configs from older builds.
pub fn seed_default_permission(existing: &str) -> Option<String> {
    let already_safe = serde_json::from_str::<Value>(existing)
        .ok()
        .and_then(|root| root.get("permission").cloned())
        .is_some_and(|permission| permission == approve_permission());
    if already_safe {
        return None;
    }
    set_permission_mode(existing, MODE_APPROVE).ok()
}

/// Install the one product-owned local MCP bridge into the app-private
/// OpenCode profile. The descriptor path is the sole environment value; the
/// bridge derives project identity from its inherited working directory.
pub fn seed_skill_mcp(
    existing: &str,
    executable: &str,
    descriptor_path: &str,
) -> Result<String, String> {
    if executable.is_empty()
        || descriptor_path.is_empty()
        || executable.contains(['\n', '\r'])
        || descriptor_path.contains(['\n', '\r'])
    {
        return Err("invalid Science Skill MCP path".into());
    }
    let mut root: Value = if existing.trim().is_empty() {
        json!({})
    } else {
        serde_json::from_str(existing)
            .map_err(|error| format!("invalid existing config: {error}"))?
    };
    if !root.is_object() {
        root = json!({});
    }
    let object = root.as_object_mut().unwrap();
    let mcp = object.entry("mcp").or_insert_with(|| json!({}));
    if !mcp.is_object() {
        *mcp = json!({});
    }
    mcp.as_object_mut().unwrap().insert(
        "spark-skill-mcp".into(),
        json!({
            "type": "local",
            "command": [executable, "--spark-skill-mcp"],
            "environment": {
                "SPARK_SKILL_MCP_DESCRIPTOR": descriptor_path
            },
            "enabled": true,
            "timeout": 5000
        }),
    );
    serde_json::to_string_pretty(&root).map_err(|error| error.to_string())
}

/// The approval mode a config encodes: None when the `permission` key was
/// never written (first run — the caller seeds the "approve" default).
#[cfg(test)]
pub fn permission_mode_of(existing: &str) -> Option<&'static str> {
    let root: Value = serde_json::from_str(existing).ok()?;
    let permission = root.get("permission")?;
    if permission == &approve_permission() {
        Some(MODE_APPROVE)
    } else {
        Some(MODE_FULL)
    }
}

/// Merge provider credentials/model into existing OpenCode config JSON.
/// Empty fields are left untouched; existing unrelated keys are preserved.
pub fn merge_config(
    existing: &str,
    provider: &str,
    api_key: &str,
    model: &str,
    base_url: Option<&str>,
) -> Result<String, String> {
    let mut root: Value = if existing.trim().is_empty() {
        json!({})
    } else {
        serde_json::from_str(existing).map_err(|e| format!("invalid existing config: {e}"))?
    };
    if !root.is_object() {
        root = json!({});
    }
    let obj = root.as_object_mut().unwrap();

    if !model.is_empty() {
        obj.insert("model".to_string(), json!(model));
    }

    if !provider.is_empty() {
        let providers = obj.entry("provider").or_insert_with(|| json!({}));
        if !providers.is_object() {
            *providers = json!({});
        }
        let pobj = providers.as_object_mut().unwrap();
        let entry = pobj.entry(provider).or_insert_with(|| json!({}));
        if !entry.is_object() {
            *entry = json!({});
        }
        let options = entry
            .as_object_mut()
            .unwrap()
            .entry("options")
            .or_insert_with(|| json!({}));
        if !options.is_object() {
            *options = json!({});
        }
        let oobj = options.as_object_mut().unwrap();
        if !api_key.is_empty() {
            oobj.insert("apiKey".to_string(), json!(api_key));
        }
        if let Some(b) = base_url {
            if !b.is_empty() {
                oobj.insert("baseURL".to_string(), json!(b));
            }
        }
    }

    serde_json::to_string_pretty(&root).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_provider_key_model_into_empty_config() {
        let out = merge_config(
            "",
            "anthropic",
            "sk-test",
            "anthropic/claude-sonnet-4-5",
            None,
        )
        .unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["model"], "anthropic/claude-sonnet-4-5");
        assert_eq!(v["provider"]["anthropic"]["options"]["apiKey"], "sk-test");
    }

    #[test]
    fn preserves_existing_unrelated_config() {
        let existing = r#"{"theme":"dark","provider":{"openai":{"options":{"apiKey":"old"}}}}"#;
        let out = merge_config(existing, "anthropic", "sk-new", "", None).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["theme"], "dark");
        assert_eq!(v["provider"]["openai"]["options"]["apiKey"], "old");
        assert_eq!(v["provider"]["anthropic"]["options"]["apiKey"], "sk-new");
    }

    #[test]
    fn sets_base_url_when_provided() {
        let out = merge_config("", "openai", "k", "openai/gpt-4o", Some("https://x/v1")).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(
            v["provider"]["openai"]["options"]["baseURL"],
            "https://x/v1"
        );
    }

    #[test]
    fn approve_mode_writes_deny_by_default_policy() {
        let out = set_permission_mode("", MODE_APPROVE).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["permission"]["*"], "ask");
        assert_eq!(v["permission"]["read"], "allow");
        assert_eq!(v["permission"]["edit"], "ask");
        assert_eq!(v["permission"]["bash"], "ask");
        assert_eq!(v["permission"]["webfetch"], "ask");
        assert_eq!(v["permission"]["websearch"], "ask");
        assert_eq!(v["permission"]["paper-search_search_*"], "deny");
        assert_eq!(v["permission"]["paper-search_read_*"], "deny");
        assert_eq!(v["permission"]["paper-search_download_*"], "deny");
        assert_eq!(v["permission"]["external_directory"], "deny");
    }

    #[test]
    fn full_mode_is_rejected() {
        let approved = set_permission_mode("", MODE_APPROVE).unwrap();
        assert!(set_permission_mode(&approved, MODE_FULL).is_err());
    }

    #[test]
    fn set_permission_mode_preserves_unrelated_keys() {
        let existing =
            r#"{"model":"anthropic/claude","provider":{"openai":{"options":{"apiKey":"k"}}}}"#;
        let out = set_permission_mode(existing, MODE_APPROVE).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["model"], "anthropic/claude");
        assert_eq!(v["provider"]["openai"]["options"]["apiKey"], "k");
    }

    #[test]
    fn set_permission_mode_rejects_unknown_mode() {
        assert!(set_permission_mode("", "off").is_err());
    }

    #[test]
    fn enforces_safe_policy_and_repairs_legacy_config() {
        let seeded = seed_default_permission("").unwrap();
        let v: Value = serde_json::from_str(&seeded).unwrap();
        assert_eq!(v["permission"]["bash"], "ask");
        assert!(seed_default_permission(&seeded).is_none());
        let repaired = seed_default_permission(r#"{"model":"m","permission":{}}"#).unwrap();
        let repaired: Value = serde_json::from_str(&repaired).unwrap();
        assert_eq!(repaired["model"], "m");
        assert_eq!(repaired["permission"]["*"], "ask");
        // Other keys survive seeding.
        let seeded2 = seed_default_permission(r#"{"model":"m"}"#).unwrap();
        let v2: Value = serde_json::from_str(&seeded2).unwrap();
        assert_eq!(v2["model"], "m");
    }

    #[test]
    fn permission_mode_of_detects_each_state() {
        // Never configured (first run) — the caller must seed the default.
        assert_eq!(permission_mode_of(""), None);
        assert_eq!(permission_mode_of(r#"{"model":"m"}"#), None);
        let approved = set_permission_mode("", MODE_APPROVE).unwrap();
        assert_eq!(permission_mode_of(&approved), Some(MODE_APPROVE));
        assert_eq!(permission_mode_of(r#"{"permission":{}}"#), Some(MODE_FULL));
    }

    #[test]
    fn seeds_fixed_skill_mcp_idempotently_and_preserves_existing_config() {
        let existing = r#"{
            "model":"provider/model",
            "mcp":{"existing":{"type":"local","command":["existing"]}},
            "permission":{"*":"ask"}
        }"#;
        let first = seed_skill_mcp(
            existing,
            "/Applications/Spark Agent.app/Contents/MacOS/Spark Agent",
            "/private/app/science-core-runtime/session/spark-skill-mcp-connection.json",
        )
        .unwrap();
        let second = seed_skill_mcp(
            &first,
            "/Applications/Spark Agent.app/Contents/MacOS/Spark Agent",
            "/private/app/science-core-runtime/session/spark-skill-mcp-connection.json",
        )
        .unwrap();
        assert_eq!(first, second);
        let value: Value = serde_json::from_str(&first).unwrap();
        assert_eq!(value["model"], "provider/model");
        assert_eq!(value["mcp"]["existing"]["command"][0], "existing");
        assert_eq!(
            value["mcp"]["spark-skill-mcp"],
            json!({
                "type": "local",
                "command": [
                    "/Applications/Spark Agent.app/Contents/MacOS/Spark Agent",
                    "--spark-skill-mcp"
                ],
                "environment": {
                    "SPARK_SKILL_MCP_DESCRIPTOR":
                        "/private/app/science-core-runtime/session/spark-skill-mcp-connection.json"
                },
                "enabled": true,
                "timeout": 5000
            })
        );
        assert_eq!(value["permission"]["*"], "ask");
        assert!(value["permission"]["spark-skill-mcp_*"].is_null());
    }
}
