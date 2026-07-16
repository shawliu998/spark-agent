// Pure merge of provider credentials/model into OpenCode config JSON.
// Used by the runtime command, which writes it into an app-private config dir.
use serde_json::{json, Value};

/// User-facing OpenCode permission states. New writes support only the safe
/// manual-approval default; legacy Full Access remains detectable so the UI can
/// tell the user to migrate it.
pub const MODE_APPROVE: &str = "approve";
pub const MODE_FULL: &str = "full";
pub const MODE_CUSTOM: &str = "custom";

/// OpenCode accepts JSONC (comments and trailing commas) for `opencode.jsonc`.
/// Parse strict JSON first for precise errors, then the JSON5-compatible
/// superset used for JSONC. Writes are normalized to strict JSON, preserving
/// every data field while intentionally discarding comments.
pub fn parse_config(existing: &str, label: &str) -> Result<Value, String> {
    if existing.trim().is_empty() {
        return Ok(json!({}));
    }
    serde_json::from_str(existing).or_else(|json_error| {
        json5::from_str(existing).map_err(|jsonc_error| {
            format!("invalid {label}: {json_error}; JSONC parse also failed: {jsonc_error}")
        })
    })
}

fn config_object_mut<'a>(
    root: &'a mut Value,
    label: &str,
) -> Result<&'a mut serde_json::Map<String, Value>, String> {
    root.as_object_mut()
        .ok_or_else(|| format!("{label} must be a JSON object"))
}

fn legacy_approve_permission() -> Value {
    // Exact policy shipped before the General Research profile. Recognizing it
    // lets startup migrate Spark-owned defaults without rewriting arbitrary
    // user permission objects.
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
        // The first curated research connector is read-only at the policy
        // boundary: metadata/search and remote reads are allowed, while every
        // download tool (which writes a PDF locally) is unavailable.
        "paper-search_search_*": "allow",
        "paper-search_read_*": "allow",
        "paper-search_download_*": "deny",
        "external_directory": "deny"
    })
}

/// High-risk command tokens gated by the retired patterned Balanced preset.
/// OpenCode command
/// permissions are glob based and last-match-wins, so every token gets both a
/// direct rule and an embedded rule for compound commands such as
/// `cd work && rm -rf output`.
const DANGEROUS_BASH: &[&str] = &[
    // Deletion and destructive filesystem/system operations.
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "truncate",
    "git clean",
    "git reset --hard",
    "sudo",
    "su",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "killall",
    "launchctl",
    "systemctl",
    "crontab",
    "osascript",
    "diskutil",
    "dd",
    // Dependency installation.
    "pip install",
    "pip3 install",
    "python -m pip install",
    "uv add",
    "uv pip install",
    "npm install",
    "npm i",
    "npm add",
    "pnpm add",
    "pnpm install",
    "yarn add",
    "bun add",
    "conda install",
    "mamba install",
    "brew install",
    "cargo install",
    "gem install",
    "apt install",
    "apt-get install",
    // Credentials, remote connections/uploads, and paid/cluster tools.
    "env",
    "printenv",
    "security",
    "ssh",
    "scp",
    "sftp",
    "rsync",
    "curl",
    "wget",
    "nc",
    "git push",
    "modal",
    "sbatch",
    "aws",
    "gcloud",
    "az",
    "kubectl",
];

fn prior_balanced_permission() -> Value {
    let mut bash = serde_json::Map::new();
    bash.insert("*".to_string(), json!("allow"));
    for token in DANGEROUS_BASH {
        bash.insert(format!("{token} *"), json!("ask"));
        bash.insert(format!("* {token} *"), json!("ask"));
    }
    for pattern in [
        "find * -delete*",
        "* find * -delete*",
        "* .env*",
        "* */.env*",
        "* ~/.ssh/*",
        "* ~/.aws/*",
        "* ~/.config/*",
        "*> ../*",
        "*>> ../*",
        "* > ../*",
        "* >> ../*",
        "cp * ../*",
        "* cp * ../*",
        "mv * ../*",
        "* mv * ../*",
        "tee ../*",
        "* tee ../*",
    ] {
        bash.insert(pattern.to_string(), json!("ask"));
    }

    json!({
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow"
        },
        "edit": "allow",
        "bash": Value::Object(bash),
        "external_directory": "ask",
        "mcp": "ask",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "skill": "allow"
    })
}

fn prior_manual_approval_permission() -> Value {
    json!({
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow"
        },
        "edit": "allow",
        "apply_patch": "ask",
        "bash": "ask",
        "external_directory": "deny",
        "mcp": "ask",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "ask",
        "websearch": "ask",
        "skill": "allow"
    })
}

fn manual_approval_permission() -> Value {
    // OpenCode 1.17.13 evaluates permission rules last-match-wins. Keep the
    // wildcard first so concrete safe workspace operations may opt in below,
    // while every unrecognised tool ID (including MCP tools) still asks.
    // `apply_patch` currently gates on `edit`; keep both asks so the pinned
    // runtime and a future tool-specific gate are covered.
    json!({
        "*": "ask",
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow",
            "mcp:*": "ask"
        },
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "lsp": "allow",
        "edit": "ask",
        "apply_patch": "ask",
        "bash": "ask",
        "external_directory": "deny",
        "mcp": "ask",
        "doom_loop": "ask",
        "question": "allow",
        "webfetch": "ask",
        "websearch": "ask",
        "skill": "allow"
    })
}

/// Exact JSON injected into the child after OpenCode has merged global and
/// project config. This does not rewrite a user's custom policy at rest.
pub fn effective_permission_floor_json() -> Result<String, String> {
    serde_json::to_string(&manual_approval_permission()).map_err(|error| error.to_string())
}

fn full_permission() -> Value {
    json!({ "*": "allow" })
}

/// Apply an explicit native OpenCode permission preset; unrelated config keys
/// (providers, models, MCP, user options) are preserved structurally.
pub fn set_permission_mode(existing: &str, mode: &str) -> Result<String, String> {
    let permission = match mode {
        MODE_APPROVE => manual_approval_permission(),
        MODE_FULL => {
            return Err(
                "Full Access is a legacy unsafe mode; switch to Manual approval".to_string(),
            )
        }
        other => return Err(format!("unknown approval mode \"{other}\"")),
    };
    let mut root = parse_config(existing, "existing OpenCode config")?;
    config_object_mut(&mut root, "existing OpenCode config")?
        .insert("permission".to_string(), permission);
    serde_json::to_string_pretty(&root).map_err(|e| e.to_string())
}

/// The approval mode a config encodes: None when the `permission` key was
/// never written or is a custom policy not represented by the two UI presets.
pub fn permission_mode_of(existing: &str) -> Result<Option<&'static str>, String> {
    let root = parse_config(existing, "OpenCode config")?;
    let root = root
        .as_object()
        .ok_or_else(|| "OpenCode config must be a JSON object".to_string())?;
    let Some(permission) = root.get("permission") else {
        return Ok(None);
    };
    if permission == &manual_approval_permission()
        || permission == &prior_manual_approval_permission()
        || permission == &prior_balanced_permission()
        || permission == &legacy_approve_permission()
    {
        Ok(Some(MODE_APPROVE))
    } else if permission == &full_permission() || permission == &json!({}) {
        Ok(Some(MODE_FULL))
    } else {
        Ok(Some(MODE_CUSTOM))
    }
}

/// Merge the bundled app-private profile into an existing OpenCode config.
/// Every explicit existing field wins. Permission is seeded when missing, and
/// exact Spark-owned legacy approve/Balanced/Full Access presets are migrated
/// to the safe manual-approval policy. Arbitrary custom permission objects win.
pub fn merge_profile_defaults(existing: &str, template: &str) -> Result<String, String> {
    let mut root = parse_config(existing, "existing OpenCode config")?;
    let defaults = parse_config(template, "bundled OpenCode profile")?;
    let root_obj = config_object_mut(&mut root, "existing OpenCode config")?;
    let defaults_obj = defaults
        .as_object()
        .ok_or_else(|| "bundled OpenCode profile must be a JSON object".to_string())?;

    for (key, value) in defaults_obj {
        match root_obj.get(key) {
            None => {
                root_obj.insert(key.clone(), value.clone());
            }
            Some(current)
                if key == "permission"
                    && (current == &legacy_approve_permission()
                        || current == &prior_manual_approval_permission()
                        || current == &prior_balanced_permission()
                        || current == &full_permission()
                        || current == &json!({})) =>
            {
                root_obj.insert(key.clone(), value.clone());
            }
            Some(_) => {}
        }
    }
    serde_json::to_string_pretty(&root).map_err(|e| e.to_string())
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
    let mut root = parse_config(existing, "existing OpenCode config")?;
    let obj = config_object_mut(&mut root, "existing OpenCode config")?;

    if !model.is_empty() {
        obj.insert("model".to_string(), json!(model));
    }

    if !provider.is_empty() {
        let providers = obj.entry("provider").or_insert_with(|| json!({}));
        let pobj = providers
            .as_object_mut()
            .ok_or_else(|| "existing OpenCode config provider must be a JSON object".to_string())?;
        let entry = pobj.entry(provider).or_insert_with(|| json!({}));
        let provider_entry = entry.as_object_mut().ok_or_else(|| {
            format!("existing OpenCode provider {provider:?} must be a JSON object")
        })?;
        let options = provider_entry.entry("options").or_insert_with(|| json!({}));
        let oobj = options.as_object_mut().ok_or_else(|| {
            format!("existing OpenCode provider {provider:?} options must be a JSON object")
        })?;
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
    fn manual_approval_mode_matches_the_safe_runtime_contract() {
        let out = set_permission_mode("", MODE_APPROVE).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["permission"]["*"], "ask");
        assert_eq!(v["permission"]["read"]["*"], "allow");
        assert_eq!(v["permission"]["read"]["*.env"], "ask");
        assert_eq!(v["permission"]["read"]["*.env.*"], "ask");
        assert_eq!(v["permission"]["read"]["*.env.example"], "allow");
        assert_eq!(v["permission"]["read"]["mcp:*"], "ask");
        assert_eq!(v["permission"]["glob"], "allow");
        assert_eq!(v["permission"]["grep"], "allow");
        assert_eq!(v["permission"]["list"], "allow");
        assert_eq!(v["permission"]["lsp"], "allow");
        assert_eq!(v["permission"]["edit"], "ask");
        assert_eq!(v["permission"]["apply_patch"], "ask");
        assert_eq!(v["permission"]["bash"], "ask");
        assert_eq!(v["permission"]["external_directory"], "deny");
        assert_eq!(v["permission"]["mcp"], "ask");
        assert_eq!(v["permission"]["doom_loop"], "ask");
        assert_eq!(v["permission"]["question"], "allow");
        assert_eq!(v["permission"]["webfetch"], "ask");
        assert_eq!(v["permission"]["websearch"], "ask");
        assert_eq!(v["permission"]["skill"], "allow");
    }

    #[test]
    fn new_full_mode_selection_is_rejected() {
        let error = set_permission_mode("", MODE_FULL).unwrap_err();
        assert!(error.contains("legacy unsafe mode"));
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
    fn permission_mode_of_detects_each_state() {
        // Never configured (first run) — profile merging seeds the default.
        assert_eq!(permission_mode_of("").unwrap(), None);
        assert_eq!(permission_mode_of(r#"{"model":"m"}"#).unwrap(), None);
        let approved = set_permission_mode("", MODE_APPROVE).unwrap();
        assert_eq!(permission_mode_of(&approved).unwrap(), Some(MODE_APPROVE));
        let full = r#"{"permission":{"*":"allow"}}"#;
        assert_eq!(permission_mode_of(full).unwrap(), Some(MODE_FULL));
        assert_eq!(
            permission_mode_of(r#"{"permission":{}}"#).unwrap(),
            Some(MODE_FULL),
            "legacy Full Access used an empty permission object"
        );
        assert_eq!(
            permission_mode_of(r#"{"permission":{"edit":"allow"}}"#).unwrap(),
            Some(MODE_CUSTOM)
        );
        assert_eq!(
            permission_mode_of(r#"{"permission":{"bash":"deny"}}"#).unwrap(),
            Some(MODE_CUSTOM)
        );
    }

    #[test]
    fn profile_defaults_preserve_every_explicit_user_value() {
        let existing = r#"{
          "default_agent": "my-agent",
          "model": "acme/custom",
          "provider": {"acme": {"options": {"apiKey": "secret", "baseURL": "https://x"}}},
          "mcp": {"lab": {"type": "local", "command": ["lab-mcp"]}},
          "permission": {"bash": "deny"},
          "custom": {"nested": true}
        }"#;
        let template = r#"{
          "default_agent": "research",
          "share": "disabled",
          "autoupdate": false,
          "permission": {"*": "allow"}
        }"#;
        let out = merge_profile_defaults(existing, template).unwrap();
        let value: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(value["default_agent"], "my-agent");
        assert_eq!(value["model"], "acme/custom");
        assert_eq!(value["provider"]["acme"]["options"]["apiKey"], "secret");
        assert_eq!(value["mcp"]["lab"]["command"][0], "lab-mcp");
        assert_eq!(value["permission"], json!({ "bash": "deny" }));
        assert_eq!(value["custom"]["nested"], true);
        assert_eq!(value["share"], "disabled");
        assert_eq!(value["autoupdate"], false);
    }

    #[test]
    fn profile_defaults_migrate_exact_legacy_permissions_only() {
        let template = json!({
            "default_agent": "research",
            "permission": manual_approval_permission(),
        });
        for permission in [
            legacy_approve_permission(),
            prior_manual_approval_permission(),
            prior_balanced_permission(),
        ] {
            let existing = json!({
                "model": "keep/me",
                "permission": permission,
            });
            let out = merge_profile_defaults(&existing.to_string(), &template.to_string()).unwrap();
            let value: Value = serde_json::from_str(&out).unwrap();
            assert_eq!(value["model"], "keep/me");
            assert_eq!(value["default_agent"], "research");
            assert_eq!(value["permission"], manual_approval_permission());
        }
    }

    #[test]
    fn profile_merge_migrates_legacy_full_access_across_restart() {
        let full = r#"{"model":"keep/me","permission":{"*":"allow"}}"#;
        let template = json!({
            "default_agent": "research",
            "permission": manual_approval_permission(),
        });
        let restarted = merge_profile_defaults(full, &template.to_string()).unwrap();
        let value: Value = serde_json::from_str(&restarted).unwrap();
        assert_eq!(value["model"], "keep/me");
        assert_eq!(value["permission"], manual_approval_permission());
        assert_eq!(permission_mode_of(&restarted).unwrap(), Some(MODE_APPROVE));
    }

    #[test]
    fn profile_merge_migrates_legacy_empty_full_across_restart() {
        let template = json!({"permission": manual_approval_permission()}).to_string();
        let existing = json!({"permission": {}}).to_string();
        let merged = merge_profile_defaults(&existing, &template).unwrap();
        let value: Value = serde_json::from_str(&merged).unwrap();
        assert_eq!(value["permission"], manual_approval_permission());
        assert_eq!(permission_mode_of(&merged).unwrap(), Some(MODE_APPROVE));
    }

    #[test]
    fn jsonc_comments_and_trailing_commas_are_accepted_and_preserved_semantically() {
        let existing = r#"{
          // Keep the user's selected model and custom policy.
          "model": "acme/custom",
          "permission": { "bash": "deny", },
        }"#;
        let template = r#"{"default_agent":"research","permission":{"*":"ask"}}"#;
        let out = merge_profile_defaults(existing, template).unwrap();
        let value: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(value["model"], "acme/custom");
        assert_eq!(value["default_agent"], "research");
        assert_eq!(value["permission"]["bash"], "deny");
        assert_eq!(permission_mode_of(existing).unwrap(), Some(MODE_CUSTOM));
    }

    #[test]
    fn malformed_or_non_object_configs_fail_closed() {
        assert!(merge_profile_defaults("[1]", "{}").is_err());
        assert!(merge_config("[1]", "openai", "secret", "", None).is_err());
        assert!(set_permission_mode("[1]", MODE_APPROVE).is_err());
        assert!(permission_mode_of("{/* unterminated").is_err());
    }

    #[test]
    fn provider_merge_rejects_non_object_user_fields_instead_of_overwriting_them() {
        assert!(
            merge_config(r#"{"provider":"custom"}"#, "openai", "secret", "", None)
                .unwrap_err()
                .contains("provider must be a JSON object")
        );
        assert!(merge_config(
            r#"{"provider":{"openai":"custom"}}"#,
            "openai",
            "secret",
            "",
            None,
        )
        .unwrap_err()
        .contains("provider \"openai\" must be a JSON object"));
        assert!(merge_config(
            r#"{"provider":{"openai":{"options":"custom"}}}"#,
            "openai",
            "secret",
            "",
            None,
        )
        .unwrap_err()
        .contains("options must be a JSON object"));
    }

    #[test]
    fn bundled_profile_permission_matches_the_rust_manual_contract() {
        let template: Value = serde_json::from_str(include_str!(
            "../../../../runtime/opencode-profile/opencode.json"
        ))
        .unwrap();
        assert_eq!(template["permission"], manual_approval_permission());
        assert_eq!(
            serde_json::from_str::<Value>(&effective_permission_floor_json().unwrap()).unwrap(),
            manual_approval_permission()
        );
    }
}
