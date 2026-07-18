//! API-key custody for the bundled OpenCode runtime.
//!
//! Spark-managed provider and curated-connector API keys live in separate OS
//! credential-manager services. Provider config contains opaque `{env:...}`
//! references and the OpenCode sidecar receives those values. Curated keyed
//! connector config is secretless: an Apple system relay reaches an already-
//! running native broker, which reads only the target key and gives it only to
//! the target connector child. OAuth records remain owned by OpenCode.

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

const PROVIDER_SERVICE: &str = "io.github.shawliu998.sparkagent.opencode-provider";
const CONNECTOR_SERVICE: &str = "io.github.shawliu998.sparkagent.opencode-connector";
const ENV_PREFIX: &str = "SPARK_OPENCODE_KEY_";
const CONNECTOR_ENV_PREFIX: &str = "SPARK_OPENCODE_CONNECTOR_KEY_";
const CONNECTOR_OWNER_ENV: &str = "SPARK_SCIENCE_CONNECTOR_OWNER";
const CONNECTOR_OWNER_V1: &str = "spark-agent/v1";
type AtomicWriter = dyn Fn(&Path, &[u8]) -> Result<(), String>;

#[derive(Clone, Copy, Debug)]
struct ConnectorCredentialSpec {
    connector_id: &'static str,
    api_key_env: &'static str,
    script_name: &'static str,
}

const CONNECTOR_CREDENTIALS: &[ConnectorCredentialSpec] = &[
    ConnectorCredentialSpec {
        connector_id: "materials-project",
        api_key_env: "MP_API_KEY",
        script_name: "mcp-materials-project",
    },
    ConnectorCredentialSpec {
        connector_id: "fred",
        api_key_env: "FRED_API_KEY",
        script_name: "fred-mcp",
    },
];

pub(crate) trait CredentialStore {
    fn get(&self, provider_id: &str) -> Result<Option<String>, String>;
    fn set(&self, provider_id: &str, secret: &str) -> Result<(), String>;
    fn delete(&self, provider_id: &str) -> Result<(), String>;
}

pub(crate) struct SystemCredentialStore;

impl SystemCredentialStore {
    fn entry(provider_id: &str) -> Result<keyring::Entry, String> {
        let provider_id = normalize_provider_id(provider_id)?;
        keyring::Entry::new(PROVIDER_SERVICE, &provider_id).map_err(|_| {
            format!("could not open the system credential entry for provider {provider_id:?}")
        })
    }
}

impl CredentialStore for SystemCredentialStore {
    fn get(&self, provider_id: &str) -> Result<Option<String>, String> {
        match Self::entry(provider_id)?.get_password() {
            Ok(secret) => Ok(Some(secret)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err(format!(
                "could not read the system credential entry for provider {provider_id:?}"
            )),
        }
    }

    fn set(&self, provider_id: &str, secret: &str) -> Result<(), String> {
        Self::entry(provider_id)?.set_password(secret).map_err(|_| {
            format!("could not save the system credential for provider {provider_id:?}")
        })
    }

    fn delete(&self, provider_id: &str) -> Result<(), String> {
        match Self::entry(provider_id)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(format!(
                "could not delete the system credential for provider {provider_id:?}"
            )),
        }
    }
}

pub(crate) struct SystemConnectorCredentialStore;

impl SystemConnectorCredentialStore {
    fn entry(connector_id: &str) -> Result<keyring::Entry, String> {
        let spec = connector_credential_spec(connector_id)?;
        keyring::Entry::new(CONNECTOR_SERVICE, spec.connector_id).map_err(|_| {
            format!(
                "could not open the system credential entry for connector {:?}",
                spec.connector_id
            )
        })
    }
}

impl CredentialStore for SystemConnectorCredentialStore {
    fn get(&self, connector_id: &str) -> Result<Option<String>, String> {
        match Self::entry(connector_id)?.get_password() {
            Ok(secret) => Ok(Some(secret)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err(format!(
                "could not read the system credential entry for connector {connector_id:?}"
            )),
        }
    }

    fn set(&self, connector_id: &str, secret: &str) -> Result<(), String> {
        Self::entry(connector_id)?
            .set_password(secret)
            .map_err(|_| {
                format!("could not save the system credential for connector {connector_id:?}")
            })
    }

    fn delete(&self, connector_id: &str) -> Result<(), String> {
        match Self::entry(connector_id)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(format!(
                "could not delete the system credential for connector {connector_id:?}"
            )),
        }
    }
}

fn connector_credential_spec(connector_id: &str) -> Result<ConnectorCredentialSpec, String> {
    CONNECTOR_CREDENTIALS
        .iter()
        .copied()
        .find(|spec| spec.connector_id == connector_id)
        .ok_or_else(|| format!("connector {connector_id:?} does not use a Spark-managed API key"))
}

pub(crate) fn managed_connector_ids() -> impl Iterator<Item = &'static str> {
    CONNECTOR_CREDENTIALS.iter().map(|spec| spec.connector_id)
}

fn connector_env_name(connector_id: &str) -> Result<String, String> {
    let spec = connector_credential_spec(connector_id)?;
    let mut hasher = Sha256::new();
    hasher.update(spec.connector_id.as_bytes());
    hasher.update(b"\0");
    hasher.update(spec.api_key_env.as_bytes());
    Ok(format!("{CONNECTOR_ENV_PREFIX}{:X}", hasher.finalize()))
}

fn connector_placeholder(connector_id: &str) -> Result<String, String> {
    Ok(format!("{{env:{}}}", connector_env_name(connector_id)?))
}

fn validate_provider_id(provider_id: &str) -> Result<(), String> {
    if provider_id.is_empty()
        || provider_id.len() > 256
        || provider_id.chars().any(char::is_control)
    {
        return Err("provider id is empty or invalid".to_string());
    }
    Ok(())
}

pub(crate) fn normalize_provider_id(provider_id: &str) -> Result<String, String> {
    let normalized = provider_id.trim_end_matches('/');
    validate_provider_id(normalized)?;
    Ok(normalized.to_string())
}

pub(crate) fn provider_env_name(provider_id: &str) -> Result<String, String> {
    let provider_id = normalize_provider_id(provider_id)?;
    let digest = Sha256::digest(provider_id.as_bytes());
    Ok(format!("{ENV_PREFIX}{digest:X}"))
}

fn provider_placeholder(provider_id: &str) -> Result<String, String> {
    Ok(format!("{{env:{}}}", provider_env_name(provider_id)?))
}

fn is_env_placeholder(value: &str) -> bool {
    value
        .strip_prefix("{env:")
        .and_then(|value| value.strip_suffix('}'))
        .is_some_and(|name| !name.is_empty())
}

fn is_file_placeholder(value: &str) -> bool {
    value
        .strip_prefix("{file:")
        .and_then(|value| value.strip_suffix('}'))
        .is_some_and(|path| !path.is_empty())
}

fn is_external_reference(value: &str) -> bool {
    is_env_placeholder(value) || is_file_placeholder(value)
}

fn normalized_config_keys(config: &Value, provider_id: &str) -> Result<Vec<String>, String> {
    let target = normalize_provider_id(provider_id)?;
    let Some(providers) = config.get("provider") else {
        return Ok(Vec::new());
    };
    let providers = providers
        .as_object()
        .ok_or_else(|| "OpenCode config provider must be a JSON object".to_string())?;
    providers
        .keys()
        .filter_map(|key| match normalize_provider_id(key) {
            Ok(normalized) if normalized == target => Some(Ok(key.clone())),
            Ok(_) => None,
            Err(error) => Some(Err(error)),
        })
        .collect()
}

fn provider_options_mut<'a>(
    config: &'a mut Value,
    config_key: &str,
) -> Result<&'a mut Map<String, Value>, String> {
    validate_provider_id(config_key)?;
    let root = config
        .as_object_mut()
        .ok_or_else(|| "OpenCode config must be a JSON object".to_string())?;
    let providers = root.entry("provider").or_insert_with(|| json!({}));
    let providers = providers
        .as_object_mut()
        .ok_or_else(|| "OpenCode config provider must be a JSON object".to_string())?;
    let provider = providers
        .entry(config_key.to_string())
        .or_insert_with(|| json!({}));
    let provider = provider
        .as_object_mut()
        .ok_or_else(|| format!("OpenCode provider {config_key:?} must be a JSON object"))?;
    let options = provider.entry("options").or_insert_with(|| json!({}));
    options
        .as_object_mut()
        .ok_or_else(|| format!("OpenCode provider {config_key:?} options must be a JSON object"))
}

fn set_provider_placeholder_at(
    config: &mut Value,
    config_key: &str,
    credential_id: &str,
) -> Result<(), String> {
    provider_options_mut(config, config_key)?.insert(
        "apiKey".to_string(),
        Value::String(provider_placeholder(credential_id)?),
    );
    Ok(())
}

fn parse_auth(text: Option<&str>) -> Result<Option<Value>, String> {
    let Some(text) = text else { return Ok(None) };
    let value: Value = serde_json::from_str(text)
        .map_err(|error| format!("invalid OpenCode auth file: {error}"))?;
    if !value.is_object() {
        return Err("OpenCode auth file must be a JSON object".to_string());
    }
    Ok(Some(value))
}

fn api_auth_secret(entry: &Value, provider_id: &str) -> Result<Option<String>, String> {
    let Some(entry) = entry.as_object() else {
        return Ok(None);
    };
    if entry.get("type").and_then(Value::as_str) != Some("api") {
        return Ok(None);
    }
    if entry
        .keys()
        .any(|key| !matches!(key.as_str(), "type" | "key"))
    {
        return Err(format!(
            "provider {provider_id:?} has an unsupported API auth record; it was left untouched"
        ));
    }
    let secret = entry
        .get("key")
        .and_then(Value::as_str)
        .filter(|key| !key.is_empty())
        .ok_or_else(|| {
            format!(
                "provider {provider_id:?} has an API auth record without a non-empty key; it was left untouched"
            )
        })?;
    Ok(Some(secret.to_string()))
}

fn remove_api_auth(auth: &mut Option<Value>, provider_id: &str) -> Result<bool, String> {
    let Some(root) = auth.as_mut() else {
        return Ok(false);
    };
    let root = root
        .as_object_mut()
        .ok_or_else(|| "OpenCode auth file must be a JSON object".to_string())?;
    let is_api = root
        .get(provider_id)
        .and_then(Value::as_object)
        .and_then(|entry| entry.get("type"))
        .and_then(Value::as_str)
        == Some("api");
    Ok(is_api && root.remove(provider_id).is_some())
}

fn normalized_auth_key(auth: &Option<Value>, provider_id: &str) -> Result<Option<String>, String> {
    let target = normalize_provider_id(provider_id)?;
    let Some(root) = auth.as_ref().and_then(Value::as_object) else {
        return Ok(None);
    };
    let mut found = None;
    for (key, entry) in root {
        if normalize_provider_id(key)? == target
            && entry
                .as_object()
                .and_then(|entry| entry.get("type"))
                .and_then(Value::as_str)
                == Some("api")
        {
            if found.is_some() {
                return Err(format!(
                    "multiple API auth records normalize to provider {target:?}"
                ));
            }
            found = Some(key.clone());
        }
    }
    Ok(found)
}

fn remove_normalized_api_auth(auth: &mut Option<Value>, provider_id: &str) -> Result<bool, String> {
    let Some(key) = normalized_auth_key(auth, provider_id)? else {
        return Ok(false);
    };
    remove_api_auth(auth, &key)
}

#[derive(Debug)]
struct MigrationPlan {
    config: Value,
    auth: Option<Value>,
    secrets_to_save: BTreeMap<String, String>,
    required_providers: BTreeSet<String>,
    external_providers: BTreeSet<String>,
    config_changed: bool,
    auth_changed: bool,
}

fn plan_migration(config_text: &str, auth_text: Option<&str>) -> Result<MigrationPlan, String> {
    let original_config = crate::opencode_config::parse_config(config_text, "OpenCode config")?;
    let original_auth = parse_auth(auth_text)?;
    let mut config = original_config.clone();
    let mut auth = original_auth.clone();
    let mut secrets_to_save = BTreeMap::<String, String>::new();
    let mut config_keys = BTreeMap::<String, String>::new();
    let mut external_providers = BTreeSet::new();

    if let Some(providers) = config.get("provider") {
        let providers = providers
            .as_object()
            .ok_or_else(|| "OpenCode config provider must be a JSON object".to_string())?;
        for (config_key, provider) in providers {
            let provider_id = normalize_provider_id(config_key)?;
            if let Some(previous) = config_keys.insert(provider_id.clone(), config_key.clone()) {
                return Err(format!(
                    "provider ids {previous:?} and {config_key:?} normalize to the same credential identity"
                ));
            }
            let Some(api_key) = provider
                .as_object()
                .and_then(|provider| provider.get("options"))
                .and_then(Value::as_object)
                .and_then(|options| options.get("apiKey"))
            else {
                continue;
            };
            let Some(api_key) = api_key.as_str() else {
                return Err(format!(
                    "OpenCode provider {config_key:?} apiKey must be a string"
                ));
            };
            let expected = provider_placeholder(&provider_id)?;
            if api_key == expected || is_external_reference(api_key) || api_key.is_empty() {
                if api_key.starts_with("{env:SPARK_OPENCODE_KEY_") && api_key != expected {
                    return Err(format!(
                        "OpenCode provider {config_key:?} has a mismatched Spark credential reference"
                    ));
                }
                if is_external_reference(api_key) && api_key != expected {
                    external_providers.insert(provider_id);
                }
                continue;
            }
            if let Some(previous) = secrets_to_save.insert(provider_id.clone(), api_key.to_string())
            {
                if previous != api_key {
                    return Err(format!(
                        "provider {provider_id:?} has conflicting API keys; neither was changed"
                    ));
                }
            }
        }
    }

    if let Some(auth_root) = auth.as_ref().and_then(Value::as_object) {
        let mut auth_ids = BTreeSet::new();
        for (auth_key, entry) in auth_root {
            let provider_id = normalize_provider_id(auth_key)?;
            let Some(secret) = api_auth_secret(entry, auth_key)? else {
                continue;
            };
            if !auth_ids.insert(provider_id.clone()) {
                return Err(format!(
                    "multiple API auth records normalize to provider {provider_id:?}"
                ));
            }
            if let Some(existing) = secrets_to_save.get(&provider_id) {
                if existing != &secret {
                    return Err(format!(
                        "provider {provider_id:?} has conflicting API keys in config and auth; neither was changed"
                    ));
                }
            } else if let Some(api_key) = config_keys.get(&provider_id).and_then(|config_key| {
                config
                    .get("provider")
                    .and_then(Value::as_object)
                    .and_then(|providers| providers.get(config_key))
                    .and_then(Value::as_object)
                    .and_then(|provider| provider.get("options"))
                    .and_then(Value::as_object)
                    .and_then(|options| options.get("apiKey"))
            }) {
                let expected = provider_placeholder(&provider_id)?;
                match api_key.as_str() {
                    Some(value) if value == expected => {}
                    Some(value) if is_external_reference(value) => {
                        return Err(format!(
                            "provider {provider_id:?} has both an external key reference and OpenCode API auth; neither was changed"
                        ));
                    }
                    Some(value) if !value.is_empty() && value != secret => {
                        return Err(format!(
                            "provider {provider_id:?} has conflicting API keys in config and auth; neither was changed"
                        ));
                    }
                    Some(_) => {}
                    None => {
                        return Err(format!(
                            "OpenCode provider {provider_id:?} apiKey must be a string"
                        ));
                    }
                }
            }
            secrets_to_save.insert(provider_id, secret);
        }
    }

    for provider_id in secrets_to_save.keys() {
        let config_key = config_keys
            .get(provider_id)
            .cloned()
            .unwrap_or_else(|| provider_id.clone());
        set_provider_placeholder_at(&mut config, &config_key, provider_id)?;
        remove_normalized_api_auth(&mut auth, provider_id)?;
    }

    let mut required_providers = BTreeSet::new();
    if let Some(providers) = config.get("provider").and_then(Value::as_object) {
        for (config_key, provider) in providers {
            let provider_id = normalize_provider_id(config_key)?;
            let Some(value) = provider
                .as_object()
                .and_then(|provider| provider.get("options"))
                .and_then(Value::as_object)
                .and_then(|options| options.get("apiKey"))
                .and_then(Value::as_str)
            else {
                continue;
            };
            let expected = provider_placeholder(&provider_id)?;
            if value == expected {
                required_providers.insert(provider_id);
            } else if value.starts_with("{env:SPARK_OPENCODE_KEY_") {
                return Err(format!(
                    "OpenCode provider {config_key:?} has a mismatched Spark credential reference"
                ));
            } else if is_external_reference(value) {
                external_providers.insert(provider_id);
            }
        }
    }

    Ok(MigrationPlan {
        config_changed: config != original_config,
        auth_changed: auth != original_auth,
        config,
        auth,
        secrets_to_save,
        required_providers,
        external_providers,
    })
}

fn read_optional(path: &Path) -> Result<Option<String>, String> {
    match std::fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("failed to read {}: {error}", path.display())),
    }
}

fn serialized(value: &Value, label: &str) -> Result<String, String> {
    serde_json::to_string_pretty(value)
        .map_err(|error| format!("failed to serialize {label}: {error}"))
}

fn set_connector_owner(config: &mut Value, connector_id: &str) -> Result<(), String> {
    let spec = connector_credential_spec(connector_id)?;
    let config = config.as_object_mut().ok_or_else(|| {
        format!(
            "connector {:?} config must be a JSON object",
            spec.connector_id
        )
    })?;
    let environment = config.entry("environment").or_insert_with(|| json!({}));
    let environment = environment.as_object_mut().ok_or_else(|| {
        format!(
            "connector {:?} environment must be a JSON object",
            spec.connector_id
        )
    })?;
    // The credential broker reads the secret directly from the OS credential
    // manager. OpenCode receives neither the value nor an environment
    // placeholder that would require the value in its parent process.
    environment.remove(spec.api_key_env);
    environment.insert(
        CONNECTOR_OWNER_ENV.to_string(),
        Value::String(CONNECTOR_OWNER_V1.to_string()),
    );
    Ok(())
}

fn connector_command_matches(connector: &Value, expected: &[String]) -> bool {
    let Some(connector) = connector.as_object() else {
        return false;
    };
    if connector.get("type").and_then(Value::as_str) != Some("local") {
        return false;
    }
    connector
        .get("command")
        .and_then(Value::as_array)
        .is_some_and(|command| {
            command.len() == expected.len()
                && command
                    .iter()
                    .zip(expected)
                    .all(|(actual, expected)| actual.as_str() == Some(expected.as_str()))
        })
}

fn connector_has_spark_claim(connector: &Value, spec: ConnectorCredentialSpec) -> bool {
    let Some(environment) = connector.get("environment").and_then(Value::as_object) else {
        return false;
    };
    environment.contains_key(CONNECTOR_OWNER_ENV)
        || environment
            .get(spec.api_key_env)
            .and_then(Value::as_str)
            .is_some_and(|value| value.starts_with("{env:SPARK_OPENCODE_CONNECTOR_KEY_"))
}

fn connector_has_strict_managed_shape(connector: &Value, spec: ConnectorCredentialSpec) -> bool {
    let Some(connector) = connector.as_object() else {
        return false;
    };
    if connector
        .keys()
        .any(|key| !matches!(key.as_str(), "type" | "command" | "enabled" | "environment"))
        || connector
            .get("enabled")
            .is_some_and(|enabled| !enabled.is_boolean())
    {
        return false;
    }
    connector
        .get("environment")
        .and_then(Value::as_object)
        .is_some_and(|environment| {
            environment
                .keys()
                .all(|key| key == spec.api_key_env || key == CONNECTOR_OWNER_ENV)
        })
}

fn validate_native_managed_command(
    spec: ConnectorCredentialSpec,
    command: &[String],
) -> Result<(), String> {
    if !crate::science_mcp::is_managed_connector_relay_command(command, spec.connector_id) {
        return Err(format!(
            "connector {:?} native relay command is invalid",
            spec.connector_id
        ));
    }
    Ok(())
}

fn validate_legacy_managed_command(
    spec: ConnectorCredentialSpec,
    command: &[String],
) -> Result<(), String> {
    if command.len() != 1 || command[0].is_empty() || !Path::new(&command[0]).is_absolute() {
        return Err(format!(
            "connector {:?} legacy command is invalid",
            spec.connector_id
        ));
    }
    #[cfg(windows)]
    let expected_name = format!("{}.exe", spec.script_name);
    #[cfg(not(windows))]
    let expected_name = spec.script_name.to_string();
    if Path::new(&command[0])
        .file_name()
        .and_then(|name| name.to_str())
        != Some(&expected_name)
    {
        return Err(format!(
            "connector {:?} legacy command does not target its managed executable",
            spec.connector_id
        ));
    }
    Ok(())
}

#[derive(Debug)]
struct ConnectorMigrationPlan {
    config: Value,
    secrets_to_save: BTreeMap<String, String>,
    required_connectors: BTreeSet<String>,
    config_changed: bool,
}

fn plan_connector_migration(
    config_text: &str,
    managed_commands: &BTreeMap<String, Vec<String>>,
    previous_managed_commands: &BTreeMap<String, Vec<String>>,
    legacy_managed_commands: &BTreeMap<String, Vec<String>>,
    managed_execution_enabled: bool,
) -> Result<ConnectorMigrationPlan, String> {
    let original_config = crate::opencode_config::parse_config(config_text, "OpenCode config")?;
    let mut config = original_config.clone();
    let mut secrets_to_save = BTreeMap::new();
    let mut required_connectors = BTreeSet::new();
    let mut canonicalize_connectors = BTreeSet::new();
    let mut disable_connectors = BTreeSet::new();

    let mcp = match config.get("mcp") {
        Some(mcp) => Some(
            mcp.as_object()
                .ok_or_else(|| "OpenCode config mcp must be a JSON object".to_string())?,
        ),
        None => None,
    };
    for spec in CONNECTOR_CREDENTIALS {
        let Some(connector) = mcp.and_then(|mcp| mcp.get(spec.connector_id)) else {
            continue;
        };
        let expected_command = managed_commands.get(spec.connector_id).ok_or_else(|| {
            format!(
                "connector {:?} is missing its native managed command",
                spec.connector_id
            )
        })?;
        validate_native_managed_command(*spec, expected_command)?;
        let previous_command = previous_managed_commands
            .get(spec.connector_id)
            .ok_or_else(|| {
                format!(
                    "connector {:?} is missing its previous dedicated managed command",
                    spec.connector_id
                )
            })?;
        validate_legacy_managed_command(*spec, previous_command)?;
        let legacy_command = legacy_managed_commands
            .get(spec.connector_id)
            .ok_or_else(|| {
                format!(
                    "connector {:?} is missing its native legacy command",
                    spec.connector_id
                )
            })?;
        validate_legacy_managed_command(*spec, legacy_command)?;
        let environment = connector.get("environment").and_then(Value::as_object);
        let marker = environment.and_then(|environment| environment.get(CONNECTOR_OWNER_ENV));
        if marker.is_some() && marker.and_then(Value::as_str) != Some(CONNECTOR_OWNER_V1) {
            return Err(format!(
                "connector {:?} has a mismatched Spark owner marker; OpenCode was not started",
                spec.connector_id
            ));
        }
        let api_key = environment
            .and_then(|environment| environment.get(spec.api_key_env))
            .and_then(Value::as_str);
        let has_spark_placeholder =
            api_key.is_some_and(|value| value.starts_with("{env:SPARK_OPENCODE_CONNECTOR_KEY_"));
        let matches_managed_command = connector_command_matches(connector, expected_command);
        let matches_previous_command = connector_command_matches(connector, previous_command);
        let matches_legacy_command = connector_command_matches(connector, legacy_command);
        let matches_relocated_relay = !matches_managed_command
            && crate::science_mcp::is_managed_connector_relay_command(
                connector
                    .get("command")
                    .and_then(Value::as_array)
                    .map(|command| {
                        command
                            .iter()
                            .filter_map(Value::as_str)
                            .map(ToString::to_string)
                            .collect::<Vec<_>>()
                    })
                    .as_deref()
                    .unwrap_or_default(),
                spec.connector_id,
            );
        if !matches_managed_command
            && !matches_previous_command
            && !matches_legacy_command
            && !matches_relocated_relay
        {
            if marker.is_some() || has_spark_placeholder {
                return Err(format!(
                    "connector {:?} claims Spark ownership but has a mismatched managed command; OpenCode was not started",
                    spec.connector_id
                ));
            }
            // A user may independently use the same MCP id. Without the exact
            // app-managed command or any Spark ownership signal, it is not ours
            // to inspect, migrate, inject into, or block startup for.
            continue;
        }
        let spark_claim = marker.is_some() || has_spark_placeholder;
        // Credential-bearing execution is release-gated independently from
        // migration. Preserve/scrub legacy secrets into the credential manager,
        // but force every Spark-owned entry to the canonical disabled relay
        // until the native approval and immutable-target boundary is complete.
        if !managed_execution_enabled
            && (spark_claim || matches_previous_command || matches_legacy_command)
        {
            canonicalize_connectors.insert(spec.connector_id.to_string());
            disable_connectors.insert(spec.connector_id.to_string());
        }

        // The immediately previous Spark release invoked the exact executable
        // in a dedicated keyed-connector environment. It is still reserved
        // app-managed state, but it must never receive a secret directly from
        // OpenCode again. Adopt a usable literal/placeholder, scrub every old
        // execution field, and rewrite to the broker relay. A malformed old
        // entry is disabled so Settings can recover it without executing the
        // unbrokered command.
        if matches_previous_command {
            let strict_shape = connector_has_strict_managed_shape(connector, *spec);
            match api_key {
                Some(value) if value == connector_placeholder(spec.connector_id)? => {
                    required_connectors.insert(spec.connector_id.to_string());
                }
                Some(value) if value.starts_with("{env:SPARK_OPENCODE_CONNECTOR_KEY_") => {
                    return Err(format!(
                        "connector {:?} has a mismatched Spark credential reference; OpenCode was not started",
                        spec.connector_id
                    ));
                }
                Some(value) if !is_external_reference(value) && !value.trim().is_empty() => {
                    secrets_to_save.insert(spec.connector_id.to_string(), value.to_string());
                    required_connectors.insert(spec.connector_id.to_string());
                }
                Some(_) => {
                    disable_connectors.insert(spec.connector_id.to_string());
                }
                None if marker.is_some() => {
                    required_connectors.insert(spec.connector_id.to_string());
                }
                None => {
                    disable_connectors.insert(spec.connector_id.to_string());
                }
            }
            if !strict_shape {
                disable_connectors.insert(spec.connector_id.to_string());
            }
            canonicalize_connectors.insert(spec.connector_id.to_string());
            continue;
        }

        // The retired shared-environment path is reserved Spark state. It must
        // never be allowed to execute again, even if a previous file acquired
        // extra fields such as LD_PRELOAD. Preserve a usable literal in the
        // credential manager when possible, discard every execution override,
        // and canonicalize the entry to a disabled native relay.
        if matches_legacy_command {
            if let Some(value) = api_key {
                if value == connector_placeholder(spec.connector_id)? {
                    required_connectors.insert(spec.connector_id.to_string());
                } else if value.starts_with("{env:SPARK_OPENCODE_CONNECTOR_KEY_") {
                    return Err(format!(
                        "connector {:?} has a mismatched Spark credential reference; OpenCode was not started",
                        spec.connector_id
                    ));
                } else if !is_external_reference(value) && !value.trim().is_empty() {
                    secrets_to_save.insert(spec.connector_id.to_string(), value.to_string());
                    required_connectors.insert(spec.connector_id.to_string());
                }
            } else if marker.is_some() {
                required_connectors.insert(spec.connector_id.to_string());
            }
            canonicalize_connectors.insert(spec.connector_id.to_string());
            disable_connectors.insert(spec.connector_id.to_string());
            continue;
        }

        if !connector_has_strict_managed_shape(connector, *spec) {
            if spark_claim {
                return Err(format!(
                    "connector {:?} has unsupported execution fields in a Spark-owned config; OpenCode was not started",
                    spec.connector_id
                ));
            }
            continue;
        }
        if marker.is_none() && !has_spark_placeholder {
            // A relay-shaped command without Spark ownership is not ours even
            // when it carries a literal. Leave same-name user entries untouched;
            // released plaintext formats used one of the exact direct commands
            // handled above, never the system relay.
            continue;
        }
        if connector
            .get("enabled")
            .is_some_and(|enabled| !enabled.is_boolean())
        {
            return Err(format!(
                "connector {:?} enabled flag must be a boolean",
                spec.connector_id
            ));
        }
        let environment = connector
            .get("environment")
            .and_then(Value::as_object)
            .ok_or_else(|| {
                format!(
                    "connector {:?} is missing its managed environment; OpenCode was not started",
                    spec.connector_id
                )
            })?;
        if environment.values().any(|value| !value.is_string()) {
            return Err(format!(
                "connector {:?} environment values must be strings",
                spec.connector_id
            ));
        }
        match environment.get(spec.api_key_env) {
            None if marker.is_some() => {
                // Canonical broker configs intentionally carry no API-key
                // field: the already-running native broker reads the keychain.
                required_connectors.insert(spec.connector_id.to_string());
            }
            None => continue,
            Some(value) => {
                let value = value.as_str().ok_or_else(|| {
                    format!(
                        "connector {:?} API-key field must be a string; OpenCode was not started",
                        spec.connector_id
                    )
                })?;
                if value.trim().is_empty() {
                    return Err(format!(
                        "connector {:?} has an empty API key; OpenCode was not started",
                        spec.connector_id
                    ));
                }
                let expected = connector_placeholder(spec.connector_id)?;
                if value == expected {
                    required_connectors.insert(spec.connector_id.to_string());
                    canonicalize_connectors.insert(spec.connector_id.to_string());
                } else if value.starts_with("{env:SPARK_OPENCODE_CONNECTOR_KEY_") {
                    return Err(format!(
                        "connector {:?} has a mismatched Spark credential reference; OpenCode was not started",
                        spec.connector_id
                    ));
                } else if is_external_reference(value) {
                    return Err(format!(
                        "connector {:?} uses an external API-key reference instead of Spark credential custody; OpenCode was not started",
                        spec.connector_id
                    ));
                } else {
                    secrets_to_save.insert(spec.connector_id.to_string(), value.to_string());
                    required_connectors.insert(spec.connector_id.to_string());
                    canonicalize_connectors.insert(spec.connector_id.to_string());
                }
            }
        }
        if matches_relocated_relay {
            canonicalize_connectors.insert(spec.connector_id.to_string());
        }
    }

    for connector_id in &canonicalize_connectors {
        let previous = config
            .get("mcp")
            .and_then(Value::as_object)
            .and_then(|mcp| mcp.get(connector_id))
            .ok_or_else(|| format!("connector {connector_id:?} disappeared during migration"))?;
        let enabled = !disable_connectors.contains(connector_id)
            && previous
                .get("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(true);
        let command = managed_commands
            .get(connector_id)
            .ok_or_else(|| format!("connector {connector_id:?} lost its native managed command"))?;
        let mut canonical = json!({
            "type": "local",
            "command": command,
            "enabled": enabled
        });
        set_connector_owner(&mut canonical, connector_id)?;
        config
            .get_mut("mcp")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| "OpenCode config mcp must be a JSON object".to_string())?
            .insert(connector_id.clone(), canonical);
    }

    Ok(ConnectorMigrationPlan {
        config_changed: config != original_config,
        config,
        secrets_to_save,
        required_connectors,
    })
}

fn write_config_outputs_secondary_first(
    config_paths: &[std::path::PathBuf],
    outputs: &[Option<String>],
    write_atomic: &AtomicWriter,
) -> Result<(), String> {
    if config_paths.len() != outputs.len() || config_paths.is_empty() {
        return Err("invalid OpenCode config transaction plan".to_string());
    }
    let originals = config_paths
        .iter()
        .map(|path| read_optional(path))
        .collect::<Result<Vec<_>, _>>()?;
    let mut written: Vec<usize> = Vec::new();
    for index in (1..config_paths.len()).chain(std::iter::once(0)) {
        if let Some(output) = &outputs[index] {
            if let Err(write_error) = write_atomic(&config_paths[index], output.as_bytes()) {
                let mut rollback_errors = Vec::new();
                for written_index in written.into_iter().rev() {
                    let rollback = match &originals[written_index] {
                        Some(original) => {
                            write_atomic(&config_paths[written_index], original.as_bytes())
                        }
                        None => match std::fs::remove_file(&config_paths[written_index]) {
                            Ok(()) => Ok(()),
                            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                            Err(error) => Err(error.to_string()),
                        },
                    };
                    if let Err(error) = rollback {
                        rollback_errors.push(format!(
                            "{}: {error}",
                            config_paths[written_index].display()
                        ));
                    }
                }
                if rollback_errors.is_empty() {
                    return Err(write_error);
                }
                return Err(format!(
                    "{write_error}; additionally failed to roll back OpenCode config transaction: {}",
                    rollback_errors.join("; ")
                ));
            }
            written.push(index);
        }
    }
    Ok(())
}

/// Migrate legacy curated-connector plaintext keys to their dedicated system
/// credential service and canonicalize every Spark-owned entry to the native
/// broker relay. Connector secrets never enter the OpenCode environment.
/// Missing credentials disable only the affected connector so Settings can
/// recover it without locking the whole runtime out.
pub(crate) fn migrate_and_collect_connector_env(
    store: &dyn CredentialStore,
    config_paths: &[std::path::PathBuf],
    managed_commands: &BTreeMap<String, Vec<String>>,
    previous_managed_commands: &BTreeMap<String, Vec<String>>,
    legacy_managed_commands: &BTreeMap<String, Vec<String>>,
    managed_execution_enabled: bool,
    write_atomic: &AtomicWriter,
) -> Result<Vec<(String, String)>, String> {
    if config_paths.is_empty() {
        return Err("no OpenCode config path was provided".to_string());
    }
    let mut plans = Vec::with_capacity(config_paths.len());
    for path in config_paths {
        let config_text = read_optional(path)?.unwrap_or_default();
        plans.push(plan_connector_migration(
            &config_text,
            managed_commands,
            previous_managed_commands,
            legacy_managed_commands,
            managed_execution_enabled,
        )?);
    }

    let mut secrets_to_save = BTreeMap::<String, String>::new();
    let mut required_connectors = BTreeSet::new();
    for plan in &plans {
        required_connectors.extend(plan.required_connectors.iter().cloned());
        for (connector_id, secret) in &plan.secrets_to_save {
            if let Some(previous) = secrets_to_save.insert(connector_id.clone(), secret.clone()) {
                if previous != *secret {
                    return Err(format!(
                        "connector {connector_id:?} has conflicting API keys across OpenCode config files; neither was changed"
                    ));
                }
            }
        }
    }

    let mut secrets_to_write = BTreeMap::new();
    let mut missing_connectors = BTreeSet::new();
    for connector_id in &required_connectors {
        let file_secret = secrets_to_save.get(connector_id);
        let stored_secret = store.get(connector_id)?;
        if let (Some(file_secret), Some(stored_secret)) = (file_secret, stored_secret.as_ref()) {
            if file_secret != stored_secret {
                return Err(format!(
                    "connector {connector_id:?} has conflicting API keys in the system credential manager and OpenCode files; neither was changed"
                ));
            }
        }
        match (file_secret, stored_secret) {
            (Some(secret), None) => {
                secrets_to_write.insert(connector_id.clone(), secret.clone());
            }
            (Some(_), Some(_)) | (None, Some(_)) => {}
            (None, None) => {
                missing_connectors.insert(connector_id.clone());
            }
        }
    }

    for plan in &mut plans {
        for connector_id in missing_connectors.intersection(&plan.required_connectors) {
            let connector = plan
                .config
                .get_mut("mcp")
                .and_then(Value::as_object_mut)
                .and_then(|mcp| mcp.get_mut(connector_id))
                .and_then(Value::as_object_mut)
                .ok_or_else(|| format!("connector {connector_id:?} disappeared during recovery"))?;
            if connector.get("enabled").and_then(Value::as_bool) != Some(false) {
                connector.insert("enabled".to_string(), Value::Bool(false));
                plan.config_changed = true;
            }
        }
    }

    let outputs = plans
        .iter()
        .map(|plan| {
            plan.config_changed
                .then(|| serialized(&plan.config, "OpenCode config"))
                .transpose()
        })
        .collect::<Result<Vec<_>, _>>()?;

    // Plaintext remains recoverable until every keychain write succeeds. A
    // later file failure is retry-safe because the newly stored value matches
    // the still-present plaintext source exactly.
    for (connector_id, secret) in &secrets_to_write {
        store.set(connector_id, secret)?;
    }
    write_config_outputs_secondary_first(config_paths, &outputs, write_atomic)?;
    Ok(Vec::new())
}

/// Save or update one allowlisted curated connector using only a command
/// derived by native code from the app-managed environment. No renderer-owned
/// command, arguments, environment, or unknown execution fields cross this
/// boundary.
pub(crate) fn save_connector_api_key(
    store: &dyn CredentialStore,
    config_paths: &[std::path::PathBuf],
    connector_id: &str,
    secret: &str,
    managed_command: &[String],
    write_atomic: &AtomicWriter,
) -> Result<(), String> {
    if config_paths.is_empty() {
        return Err("no OpenCode config path was provided".to_string());
    }
    let spec = connector_credential_spec(connector_id)?;
    let secret = secret.trim();
    if secret.is_empty() {
        return Err("API key must not be empty".to_string());
    }
    validate_native_managed_command(spec, managed_command)?;
    let mut managed_config = json!({
        "type": "local",
        "command": managed_command,
        "enabled": true
    });
    set_connector_owner(&mut managed_config, spec.connector_id)?;

    let mut outputs = Vec::with_capacity(config_paths.len());
    for (index, path) in config_paths.iter().enumerate() {
        let config_text = read_optional(path)?.unwrap_or_default();
        let mut config = crate::opencode_config::parse_config(&config_text, "OpenCode config")?;
        let original_config = config.clone();
        let root = config
            .as_object_mut()
            .ok_or_else(|| "OpenCode config must be a JSON object".to_string())?;
        let mcp = root.entry("mcp").or_insert_with(|| json!({}));
        let mcp = mcp
            .as_object_mut()
            .ok_or_else(|| "OpenCode config mcp must be a JSON object".to_string())?;
        if index == 0 {
            mcp.insert(spec.connector_id.to_string(), managed_config.clone());
        } else {
            mcp.remove(spec.connector_id);
        }
        outputs.push(
            (index == 0 || config != original_config)
                .then(|| serialized(&config, "OpenCode config"))
                .transpose()?,
        );
    }

    let previous_secret = store.get(spec.connector_id)?;
    store.set(spec.connector_id, secret)?;
    if let Err(write_error) =
        write_config_outputs_secondary_first(config_paths, &outputs, write_atomic)
    {
        let rollback = match previous_secret {
            Some(previous_secret) => store.set(spec.connector_id, &previous_secret),
            None => store.delete(spec.connector_id),
        };
        return match rollback {
            Ok(()) => Err(write_error),
            Err(rollback_error) => Err(format!(
                "{write_error}; additionally failed to restore the prior system credential state: {rollback_error}"
            )),
        };
    }
    Ok(())
}

/// Remove all file-backed references before deleting the connector's keychain
/// item. Secondary legacy configs are cleaned first so a failed effective-file
/// write always leaves the current live reference and credential intact.
pub(crate) fn remove_connector_api_key(
    store: &dyn CredentialStore,
    config_paths: &[std::path::PathBuf],
    connector_id: &str,
    managed_command: &[String],
    write_atomic: &AtomicWriter,
) -> Result<(), String> {
    if config_paths.is_empty() {
        return Err("no OpenCode config path was provided".to_string());
    }
    let spec = connector_credential_spec(connector_id)?;
    validate_native_managed_command(spec, managed_command)?;
    let mut outputs = Vec::with_capacity(config_paths.len());
    let mut saw_entry = false;
    let mut delete_credential = false;
    for path in config_paths {
        let config_text = read_optional(path)?.unwrap_or_default();
        let mut config = crate::opencode_config::parse_config(&config_text, "OpenCode config")?;
        let original_config = config.clone();
        if let Some(mcp) = config.get_mut("mcp") {
            let mcp = mcp
                .as_object_mut()
                .ok_or_else(|| "OpenCode config mcp must be a JSON object".to_string())?;
            if let Some(connector) = mcp.get(spec.connector_id) {
                saw_entry = true;
                delete_credential |= connector_command_matches(connector, managed_command)
                    || connector_has_spark_claim(connector, spec);
            }
            // Removal is an explicit user action, so it may remove a same-name
            // unowned config. Credential deletion remains ownership-aware.
            mcp.remove(spec.connector_id);
        }
        outputs.push(
            (config != original_config)
                .then(|| serialized(&config, "OpenCode config"))
                .transpose()?,
        );
    }
    write_config_outputs_secondary_first(config_paths, &outputs, write_atomic)?;
    if delete_credential || !saw_entry {
        store.delete(spec.connector_id)
    } else {
        Ok(())
    }
}

/// Import an external OpenCode auth document without ever copying its API keys
/// into app-private plaintext storage. The source is planned entirely in
/// memory, secrets are durably adopted by the credential manager, configs get
/// only Spark env placeholders, and the destination auth file receives only
/// OAuth/non-API records.
pub(crate) fn import_auth_secure(
    store: &dyn CredentialStore,
    config_paths: &[std::path::PathBuf],
    destination_auth_path: &Path,
    imported_auth: &str,
    write_atomic: &AtomicWriter,
) -> Result<(), String> {
    if config_paths.is_empty() {
        return Err("no OpenCode config path was provided".to_string());
    }

    let mut plans = Vec::with_capacity(config_paths.len());
    for (index, path) in config_paths.iter().enumerate() {
        let config_text = read_optional(path)?.unwrap_or_default();
        plans.push(plan_migration(
            &config_text,
            (index == 0).then_some(imported_auth),
        )?);
    }

    let mut secrets_to_save = BTreeMap::<String, String>::new();
    let mut required_providers = BTreeSet::new();
    let mut external_providers = BTreeSet::new();
    for plan in &plans {
        required_providers.extend(plan.required_providers.iter().cloned());
        external_providers.extend(plan.external_providers.iter().cloned());
        for (provider_id, secret) in &plan.secrets_to_save {
            if let Some(previous) = secrets_to_save.insert(provider_id.clone(), secret.clone()) {
                if previous != *secret {
                    return Err(format!(
                        "provider {provider_id:?} has conflicting API keys across imported auth and OpenCode config files; import was not changed"
                    ));
                }
            }
        }
    }
    if let Some(provider_id) = secrets_to_save
        .keys()
        .find(|provider_id| external_providers.contains(*provider_id))
    {
        return Err(format!(
            "provider {provider_id:?} has both imported API auth and an external key reference; import was not changed"
        ));
    }

    let mut secrets_to_write = BTreeMap::new();
    let mut available_providers = BTreeSet::new();
    for (provider_id, secret) in &secrets_to_save {
        match store.get(provider_id)? {
            Some(existing) if existing != *secret => {
                return Err(format!(
                    "provider {provider_id:?} has conflicting API keys in the system credential manager and imported files; import was not changed"
                ));
            }
            Some(_) => {
                available_providers.insert(provider_id.clone());
            }
            None => {
                secrets_to_write.insert(provider_id.clone(), secret.clone());
            }
        }
    }
    for provider_id in &required_providers {
        if secrets_to_save.contains_key(provider_id) || available_providers.contains(provider_id) {
            continue;
        }
        if store.get(provider_id)?.is_none() {
            return Err(format!(
                "provider {provider_id:?} references a missing system credential; import was not changed"
            ));
        }
    }

    let config_outputs = plans
        .iter()
        .map(|plan| {
            plan.config_changed
                .then(|| serialized(&plan.config, "OpenCode config"))
                .transpose()
        })
        .collect::<Result<Vec<_>, _>>()?;
    let sanitized_auth = plans[0]
        .auth
        .as_ref()
        .ok_or_else(|| "imported OpenCode auth file is missing".to_string())?;
    let auth_output = serialized(sanitized_auth, "OpenCode auth file")?;

    // As with startup migration, file-backed plaintext remains authoritative
    // until every required keychain write succeeds. The imported source is
    // never written to the destination at any point.
    for (provider_id, secret) in &secrets_to_write {
        store.set(provider_id, secret)?;
    }
    for (path, output) in config_paths.iter().zip(config_outputs) {
        if let Some(output) = output {
            write_atomic(path, output.as_bytes())?;
        }
    }
    write_atomic(destination_auth_path, auth_output.as_bytes())
}

pub(crate) fn migrate_and_collect_env(
    store: &dyn CredentialStore,
    config_paths: &[std::path::PathBuf],
    auth_path: &Path,
    write_atomic: &AtomicWriter,
) -> Result<Vec<(String, String)>, String> {
    if config_paths.is_empty() {
        return Err("no OpenCode config path was provided".to_string());
    }
    let auth_text = read_optional(auth_path)?;
    let mut plans = Vec::with_capacity(config_paths.len());
    for (index, path) in config_paths.iter().enumerate() {
        let config_text = read_optional(path)?.unwrap_or_default();
        plans.push(plan_migration(
            &config_text,
            (index == 0).then_some(auth_text.as_deref()).flatten(),
        )?);
    }

    let mut secrets_to_save = BTreeMap::<String, String>::new();
    let mut required_providers = BTreeSet::new();
    let mut external_providers = BTreeSet::new();
    for plan in &plans {
        required_providers.extend(plan.required_providers.iter().cloned());
        external_providers.extend(plan.external_providers.iter().cloned());
        for (provider_id, secret) in &plan.secrets_to_save {
            if let Some(previous) = secrets_to_save.insert(provider_id.clone(), secret.clone()) {
                if previous != *secret {
                    return Err(format!(
                        "provider {provider_id:?} has conflicting API keys across OpenCode config files; neither was changed"
                    ));
                }
            }
        }
    }
    if let Some(provider_id) = secrets_to_save
        .keys()
        .find(|provider_id| external_providers.contains(*provider_id))
    {
        return Err(format!(
            "provider {provider_id:?} has both plaintext API auth and an external key reference; neither was changed"
        ));
    }

    // Startup/import migration is implicit: it may adopt a missing credential
    // or confirm an identical one, but it must never overwrite an independently
    // stored value. Explicit Settings saves use `save_provider_api_key` instead.
    let mut secrets_to_write = BTreeMap::new();
    for (provider_id, secret) in &secrets_to_save {
        match store.get(provider_id)? {
            Some(existing) if existing != *secret => {
                return Err(format!(
                    "provider {provider_id:?} has conflicting API keys in the system credential manager and OpenCode files; neither was changed"
                ));
            }
            Some(_) => {}
            None => {
                secrets_to_write.insert(provider_id.clone(), secret.clone());
            }
        }
    }

    let config_outputs = plans
        .iter()
        .map(|plan| {
            plan.config_changed
                .then(|| serialized(&plan.config, "OpenCode config"))
                .transpose()
        })
        .collect::<Result<Vec<_>, _>>()?;
    let auth_output = plans[0]
        .auth_changed
        .then(|| {
            plans[0]
                .auth
                .as_ref()
                .map(|auth| serialized(auth, "OpenCode auth file"))
        })
        .flatten()
        .transpose()?;

    let mut env = BTreeMap::new();
    for provider_id in &required_providers {
        let secret = match secrets_to_save.get(provider_id) {
            Some(secret) => secret.clone(),
            None => store.get(provider_id)?.ok_or_else(|| {
                format!(
                    "provider {provider_id:?} references a missing system credential; OpenCode was not started"
                )
            })?,
        };
        env.insert(provider_env_name(provider_id)?, secret);
    }

    // Durable keychain writes always happen before either plaintext source is
    // removed. A partial failure can leave only an inaccessible extra keychain
    // item; it never loses the original file-backed secret.
    for (provider_id, secret) in &secrets_to_write {
        store.set(provider_id, secret)?;
    }
    for (path, output) in config_paths.iter().zip(config_outputs) {
        if let Some(output) = output {
            write_atomic(path, output.as_bytes())?;
        }
    }
    if let Some(auth_output) = auth_output {
        write_atomic(auth_path, auth_output.as_bytes())?;
    }
    Ok(env.into_iter().collect())
}

pub(crate) fn save_provider_api_key(
    store: &dyn CredentialStore,
    config_paths: &[std::path::PathBuf],
    auth_path: &Path,
    provider_id: &str,
    secret: &str,
    write_atomic: &AtomicWriter,
) -> Result<(), String> {
    if config_paths.is_empty() {
        return Err("no OpenCode config path was provided".to_string());
    }
    let provider_id = normalize_provider_id(provider_id)?;
    if secret.trim().is_empty() {
        return Err("API key must not be empty".to_string());
    }
    let mut config_outputs = Vec::with_capacity(config_paths.len());
    for (index, path) in config_paths.iter().enumerate() {
        let config_text = read_optional(path)?.unwrap_or_default();
        let mut config = crate::opencode_config::parse_config(&config_text, "OpenCode config")?;
        let keys = normalized_config_keys(&config, &provider_id)?;
        if keys.len() > 1 {
            return Err(format!(
                "multiple OpenCode config entries normalize to provider {provider_id:?}"
            ));
        }
        if let Some(config_key) = keys.first() {
            set_provider_placeholder_at(&mut config, config_key, &provider_id)?;
            config_outputs.push(Some(serialized(&config, "OpenCode config")?));
        } else if index == 0 {
            set_provider_placeholder_at(&mut config, &provider_id, &provider_id)?;
            config_outputs.push(Some(serialized(&config, "OpenCode config")?));
        } else {
            config_outputs.push(None);
        }
    }

    let auth_text = read_optional(auth_path)?;
    let mut auth = parse_auth(auth_text.as_deref())?;
    if let Some(auth_key) = normalized_auth_key(&auth, &provider_id)? {
        if let Some(entry) = auth
            .as_ref()
            .and_then(Value::as_object)
            .and_then(|auth| auth.get(&auth_key))
        {
            // Metadata-bearing API auth is operational provider state. This
            // secure-storage slice cannot represent it without loss.
            api_auth_secret(entry, &auth_key)?;
        }
    }
    let auth_changed = remove_normalized_api_auth(&mut auth, &provider_id)?;
    let auth_output = auth_changed
        .then(|| {
            auth.as_ref()
                .map(|auth| serialized(auth, "OpenCode auth file"))
        })
        .flatten()
        .transpose()?;

    // Preserve the exact credential-manager state so an atomic file-write
    // failure cannot make a rejected Settings save take effect on restart.
    // File outputs are planned before this point and `write_atomic` guarantees
    // that a failed individual write does not replace its destination.
    let previous_secret = store.get(&provider_id)?;
    store.set(&provider_id, secret)?;
    let write_result = (|| {
        for (path, output) in config_paths.iter().zip(config_outputs) {
            if let Some(output) = output {
                write_atomic(path, output.as_bytes())?;
            }
        }
        if let Some(auth_output) = auth_output {
            write_atomic(auth_path, auth_output.as_bytes())?;
        }
        Ok(())
    })();
    if let Err(write_error) = write_result {
        let rollback = match previous_secret {
            Some(previous_secret) => store.set(&provider_id, &previous_secret),
            None => store.delete(&provider_id),
        };
        return match rollback {
            Ok(()) => Err(write_error),
            Err(rollback_error) => Err(format!(
                "{write_error}; additionally failed to restore the prior system credential state: {rollback_error}"
            )),
        };
    }
    Ok(())
}

pub(crate) fn remove_provider_api_key(
    store: &dyn CredentialStore,
    config_paths: &[std::path::PathBuf],
    auth_path: &Path,
    provider_id: &str,
    remove_provider_config: bool,
    write_atomic: &AtomicWriter,
) -> Result<(), String> {
    if config_paths.is_empty() {
        return Err("no OpenCode config path was provided".to_string());
    }
    let provider_id = normalize_provider_id(provider_id)?;
    let mut config_outputs = Vec::with_capacity(config_paths.len());
    for path in config_paths {
        let config_text = read_optional(path)?.unwrap_or_default();
        let mut config = crate::opencode_config::parse_config(&config_text, "OpenCode config")?;
        let original_config = config.clone();
        let keys = normalized_config_keys(&config, &provider_id)?;
        if keys.len() > 1 {
            return Err(format!(
                "multiple OpenCode config entries normalize to provider {provider_id:?}"
            ));
        }
        if let Some(config_key) = keys.first() {
            if let Some(providers) = config.get_mut("provider") {
                let providers = providers
                    .as_object_mut()
                    .ok_or_else(|| "OpenCode config provider must be a JSON object".to_string())?;
                if remove_provider_config {
                    providers.remove(config_key);
                } else if let Some(provider) = providers.get_mut(config_key) {
                    let provider = provider.as_object_mut().ok_or_else(|| {
                        format!("OpenCode provider {config_key:?} must be a JSON object")
                    })?;
                    if let Some(options) = provider.get_mut("options") {
                        let options = options.as_object_mut().ok_or_else(|| {
                            format!(
                                "OpenCode provider {config_key:?} options must be a JSON object"
                            )
                        })?;
                        options.remove("apiKey");
                    }
                    if provider
                        .get("options")
                        .and_then(Value::as_object)
                        .is_some_and(Map::is_empty)
                    {
                        provider.remove("options");
                    }
                    if provider.is_empty() {
                        providers.remove(config_key);
                    }
                }
            }
        }
        config_outputs.push(
            (config != original_config)
                .then(|| serialized(&config, "OpenCode config"))
                .transpose()?,
        );
    }

    let auth_text = read_optional(auth_path)?;
    let mut auth = parse_auth(auth_text.as_deref())?;
    let auth_changed = remove_normalized_api_auth(&mut auth, &provider_id)?;
    let auth_output = auth_changed
        .then(|| {
            auth.as_ref()
                .map(|auth| serialized(auth, "OpenCode auth file"))
        })
        .flatten()
        .transpose()?;

    // Remove every live reference first. If credential deletion fails, the
    // stale keychain item is inaccessible because no config placeholder or API
    // auth record remains.
    for (path, output) in config_paths.iter().zip(config_outputs) {
        if let Some(output) = output {
            write_atomic(path, output.as_bytes())?;
        }
    }
    if let Some(auth_output) = auth_output {
        write_atomic(auth_path, auth_output.as_bytes())?;
    }
    store.delete(&provider_id)
}

/// Roll back a provider API record immediately after a failed login-finalize
/// transaction. This is intentionally scoped to that fresh callback path:
/// OAuth/non-API entries are never removed, and no failed keychain migration
/// leaves the newly supplied API key in app-private plaintext.
pub(crate) fn rollback_provider_api_auth(
    auth_path: &Path,
    provider_id: &str,
    write_atomic: &AtomicWriter,
) -> Result<bool, String> {
    let provider_id = normalize_provider_id(provider_id)?;
    let auth_text = read_optional(auth_path)?;
    let mut auth = parse_auth(auth_text.as_deref())?;
    let Some(auth_key) = normalized_auth_key(&auth, &provider_id)? else {
        return Ok(false);
    };
    let root = auth
        .as_mut()
        .and_then(Value::as_object_mut)
        .ok_or_else(|| "OpenCode auth file must be a JSON object".to_string())?;
    root.remove(&auth_key);
    let output = serialized(
        auth.as_ref()
            .ok_or_else(|| "OpenCode auth file is missing".to_string())?,
        "OpenCode auth file",
    )?;
    write_atomic(auth_path, output.as_bytes())?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Mutex;

    static NEXT_ROOT: AtomicU64 = AtomicU64::new(0);

    #[derive(Default)]
    struct FakeStore {
        values: Mutex<BTreeMap<String, String>>,
        fail_get: bool,
        fail_set: bool,
        fail_delete: bool,
    }

    impl CredentialStore for FakeStore {
        fn get(&self, provider_id: &str) -> Result<Option<String>, String> {
            if self.fail_get {
                return Err("fake read failure".to_string());
            }
            Ok(self.values.lock().unwrap().get(provider_id).cloned())
        }

        fn set(&self, provider_id: &str, secret: &str) -> Result<(), String> {
            if self.fail_set {
                return Err("fake save failure".to_string());
            }
            self.values
                .lock()
                .unwrap()
                .insert(provider_id.to_string(), secret.to_string());
            Ok(())
        }

        fn delete(&self, provider_id: &str) -> Result<(), String> {
            if self.fail_delete {
                return Err("fake delete failure".to_string());
            }
            self.values.lock().unwrap().remove(provider_id);
            Ok(())
        }
    }

    fn root(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "spark-credential-{label}-{}-{}",
            std::process::id(),
            NEXT_ROOT.fetch_add(1, Ordering::Relaxed)
        ))
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    fn atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
        std::fs::create_dir_all(path.parent().unwrap()).map_err(|error| error.to_string())?;
        std::fs::write(path, bytes).map_err(|error| error.to_string())
    }

    fn managed_connector_command(connector_id: &str) -> Vec<String> {
        let socket_name = match connector_id {
            "materials-project" => "mp.sock",
            "fred" => "fred.sock",
            _ => panic!("unexpected keyed connector in test"),
        };
        vec![
            "/usr/bin/nc".to_string(),
            "-U".to_string(),
            format!("/private/tmp/spark-mcp-0123456789abcdef/{socket_name}"),
        ]
    }

    fn managed_connector_commands() -> BTreeMap<String, Vec<String>> {
        managed_connector_ids()
            .map(|connector_id| {
                (
                    connector_id.to_string(),
                    managed_connector_command(connector_id),
                )
            })
            .collect()
    }

    fn previous_managed_connector_command(connector_id: &str) -> Vec<String> {
        let script = connector_credential_spec(connector_id).unwrap().script_name;
        vec![format!("/previous-dedicated/{connector_id}/bin/{script}")]
    }

    fn previous_managed_connector_commands() -> BTreeMap<String, Vec<String>> {
        managed_connector_ids()
            .map(|connector_id| {
                (
                    connector_id.to_string(),
                    previous_managed_connector_command(connector_id),
                )
            })
            .collect()
    }

    fn legacy_managed_connector_command(connector_id: &str) -> Vec<String> {
        let script = connector_credential_spec(connector_id).unwrap().script_name;
        vec![format!("/legacy-shared/bin/{script}")]
    }

    fn legacy_managed_connector_commands() -> BTreeMap<String, Vec<String>> {
        managed_connector_ids()
            .map(|connector_id| {
                (
                    connector_id.to_string(),
                    legacy_managed_connector_command(connector_id),
                )
            })
            .collect()
    }

    fn plan_connector_migration(
        config_text: &str,
        managed_commands: &BTreeMap<String, Vec<String>>,
        legacy_managed_commands: &BTreeMap<String, Vec<String>>,
    ) -> Result<ConnectorMigrationPlan, String> {
        super::plan_connector_migration(
            config_text,
            managed_commands,
            &previous_managed_connector_commands(),
            legacy_managed_commands,
            true,
        )
    }

    fn plan_security_gated_connector_migration(
        config_text: &str,
        managed_commands: &BTreeMap<String, Vec<String>>,
        legacy_managed_commands: &BTreeMap<String, Vec<String>>,
    ) -> Result<ConnectorMigrationPlan, String> {
        super::plan_connector_migration(
            config_text,
            managed_commands,
            &previous_managed_connector_commands(),
            legacy_managed_commands,
            false,
        )
    }

    fn migrate_and_collect_connector_env(
        store: &dyn CredentialStore,
        config_paths: &[std::path::PathBuf],
        managed_commands: &BTreeMap<String, Vec<String>>,
        legacy_managed_commands: &BTreeMap<String, Vec<String>>,
        write_atomic: &AtomicWriter,
    ) -> Result<Vec<(String, String)>, String> {
        super::migrate_and_collect_connector_env(
            store,
            config_paths,
            managed_commands,
            &previous_managed_connector_commands(),
            legacy_managed_commands,
            true,
            write_atomic,
        )
    }

    #[test]
    fn migrates_config_and_api_auth_after_durable_save_and_preserves_oauth() {
        let dir = root("migrate");
        let config = dir.join("opencode.jsonc");
        let auth = dir.join("auth.json");
        write(
            &config,
            "// jsonc\n{provider:{acme:{name:'Acme',options:{apiKey:'config-secret',baseURL:'https://x',},models:{m:{name:'M'},},},},}",
        );
        write(
            &auth,
            r#"{"acme":{"type":"api","key":"config-secret"},"oauth":{"type":"oauth","refresh":"exact-token","nested":{"n":1}}}"#,
        );
        let store = FakeStore::default();

        let env =
            migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic).unwrap();

        let config_value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(config_value["provider"]["acme"]["name"], "Acme");
        assert_eq!(
            config_value["provider"]["acme"]["options"]["baseURL"],
            "https://x"
        );
        assert_eq!(config_value["provider"]["acme"]["models"]["m"]["name"], "M");
        let placeholder = provider_placeholder("acme").unwrap();
        assert_eq!(
            config_value["provider"]["acme"]["options"]["apiKey"],
            placeholder
        );
        let auth_value: Value =
            serde_json::from_str(&std::fs::read_to_string(&auth).unwrap()).unwrap();
        assert!(auth_value.get("acme").is_none());
        assert_eq!(
            auth_value["oauth"],
            json!({"type":"oauth","refresh":"exact-token","nested":{"n":1}})
        );
        assert_eq!(env.len(), 1);
        assert_eq!(env[0].1, "config-secret");
        assert_eq!(
            store.values.lock().unwrap().get("acme").map(String::as_str),
            Some("config-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn conflicting_sources_fail_without_mutating_files_or_store() {
        let dir = root("conflict");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text = r#"{"provider":{"acme":{"options":{"apiKey":"one"}}}}"#;
        let auth_text = r#"{"acme":{"type":"api","key":"two"}}"#;
        write(&config, config_text);
        write(&auth, auth_text);
        let store = FakeStore::default();

        let error = migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic)
            .unwrap_err();

        assert!(error.contains("conflicting API keys"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), auth_text);
        assert!(store.values.lock().unwrap().is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn normalized_config_and_auth_collisions_are_rejected_during_planning() {
        let config_error =
            plan_migration(r#"{"provider":{"acme":{},"acme/":{}}}"#, None).unwrap_err();
        assert!(config_error.contains("normalize to the same credential identity"));

        let auth_error = plan_migration(
            "{}",
            Some(r#"{"acme":{"type":"api","key":"same"},"acme/":{"type":"api","key":"same"}}"#),
        )
        .unwrap_err();
        assert!(auth_error.contains("multiple API auth records normalize"));
    }

    #[test]
    fn implicit_migration_never_overwrites_a_conflicting_keychain_value() {
        let dir = root("keychain-conflict");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text = r#"{"provider":{"acme":{"options":{"apiKey":"file-secret"}}}}"#;
        let auth_text = r#"{"oauth":{"type":"oauth","refresh":"keep"}}"#;
        write(&config, config_text);
        write(&auth, auth_text);
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "acme".to_string(),
                "keychain-secret".to_string(),
            )])),
            ..Default::default()
        };

        let error = migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic)
            .unwrap_err();

        assert!(error.contains("system credential manager"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), auth_text);
        assert_eq!(
            store.values.lock().unwrap().get("acme").map(String::as_str),
            Some("keychain-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn missing_referenced_key_fails_before_any_file_write() {
        let dir = root("missing");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let text = json!({
            "provider": {"acme": {"options": {"apiKey": provider_placeholder("acme").unwrap()}}}
        })
        .to_string();
        write(&config, &text);
        let store = FakeStore::default();

        let error = migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic)
            .unwrap_err();

        assert!(error.contains("missing system credential"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), text);
        assert!(!auth.exists());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn failed_keychain_save_leaves_plaintext_sources_untouched() {
        let dir = root("save-failure");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let text = r#"{"provider":{"acme":{"options":{"apiKey":"still-here"}}}}"#;
        write(&config, text);
        let store = FakeStore {
            fail_set: true,
            ..Default::default()
        };

        assert!(
            migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic,)
                .is_err()
        );
        assert_eq!(std::fs::read_to_string(&config).unwrap(), text);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn secure_save_preserves_every_custom_provider_field() {
        let dir = root("custom-save");
        let config = dir.join("opencode.jsonc");
        let auth = dir.join("auth.json");
        write(
            &config,
            "{provider:{lab:{name:'Lab',npm:'@ai-sdk/openai-compatible',options:{baseURL:'http://lab/v1',headers:{'X-Lab':'yes'},},models:{alpha:{name:'Alpha'}},custom:{keep:true},},},}",
        );
        let store = FakeStore::default();

        save_provider_api_key(
            &store,
            std::slice::from_ref(&config),
            &auth,
            "lab",
            "new-secret",
            &atomic,
        )
        .unwrap();

        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(value["provider"]["lab"]["name"], "Lab");
        assert_eq!(value["provider"]["lab"]["npm"], "@ai-sdk/openai-compatible");
        assert_eq!(
            value["provider"]["lab"]["options"]["baseURL"],
            "http://lab/v1"
        );
        assert_eq!(
            value["provider"]["lab"]["options"]["headers"]["X-Lab"],
            "yes"
        );
        assert_eq!(value["provider"]["lab"]["models"]["alpha"]["name"], "Alpha");
        assert_eq!(value["provider"]["lab"]["custom"]["keep"], true);
        assert_eq!(
            value["provider"]["lab"]["options"]["apiKey"],
            provider_placeholder("lab").unwrap()
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn failed_config_write_restores_the_existing_provider_credential() {
        let dir = root("existing-key-write-failure");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text = json!({
            "provider": {"acme": {"options": {
                "apiKey": provider_placeholder("acme").unwrap()
            }}}
        })
        .to_string();
        write(&config, &config_text);
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "acme".to_string(),
                "previous-secret".to_string(),
            )])),
            ..Default::default()
        };
        let fail_write = |_path: &Path, _bytes: &[u8]| -> Result<(), String> {
            Err("fake config write failure".to_string())
        };

        let error = save_provider_api_key(
            &store,
            std::slice::from_ref(&config),
            &auth,
            "acme",
            "replacement-secret",
            &fail_write,
        )
        .unwrap_err();

        assert!(error.contains("fake config write failure"));
        assert_eq!(
            store.values.lock().unwrap().get("acme").map(String::as_str),
            Some("previous-secret")
        );
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn failed_config_write_removes_a_new_provider_credential() {
        let dir = root("new-key-write-failure");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text = r#"{"theme":"keep"}"#;
        write(&config, config_text);
        let store = FakeStore::default();
        let fail_write = |_path: &Path, _bytes: &[u8]| -> Result<(), String> {
            Err("fake config write failure".to_string())
        };

        let error = save_provider_api_key(
            &store,
            std::slice::from_ref(&config),
            &auth,
            "acme",
            "new-secret",
            &fail_write,
        )
        .unwrap_err();

        assert!(error.contains("fake config write failure"));
        assert!(!store.values.lock().unwrap().contains_key("acme"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn deletion_failure_leaves_only_an_unreferenced_keychain_item() {
        let dir = root("delete-failure");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        write(
            &config,
            &json!({
                "provider": {"acme": {"name":"Acme", "options": {
                    "apiKey": provider_placeholder("acme").unwrap(), "baseURL":"https://x"
                }}}
            })
            .to_string(),
        );
        write(&auth, r#"{"oauth":{"type":"oauth","refresh":"keep"}}"#);
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([("acme".to_string(), "stale".to_string())])),
            fail_delete: true,
            ..Default::default()
        };

        assert!(remove_provider_api_key(
            &store,
            std::slice::from_ref(&config),
            &auth,
            "acme",
            false,
            &atomic,
        )
        .is_err());

        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert!(value["provider"]["acme"]["options"].get("apiKey").is_none());
        assert_eq!(value["provider"]["acme"]["options"]["baseURL"], "https://x");
        assert_eq!(
            store.values.lock().unwrap().get("acme").map(String::as_str),
            Some("stale")
        );
        let auth_value: Value =
            serde_json::from_str(&std::fs::read_to_string(&auth).unwrap()).unwrap();
        assert_eq!(auth_value["oauth"]["refresh"], "keep");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn metadata_bearing_api_auth_fails_closed_and_import_preflight_rejects_it() {
        let dir = root("auth-metadata");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text = r#"{"theme":"keep"}"#;
        let auth_text = r#"{"azure":{"type":"api","key":"secret","resource":"deployment"}}"#;
        write(&config, config_text);
        write(&auth, auth_text);
        let store = FakeStore::default();

        let error = migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic)
            .unwrap_err();
        assert!(error.contains("unsupported API auth record"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), auth_text);
        assert!(store.values.lock().unwrap().is_empty());

        let import_error = import_auth_secure(
            &store,
            std::slice::from_ref(&config),
            &auth,
            auth_text,
            &atomic,
        )
        .unwrap_err();
        assert!(import_error.contains("unsupported API auth record"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn explicit_save_does_not_overwrite_metadata_bearing_api_auth() {
        let dir = root("metadata-save");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text = r#"{"theme":"keep"}"#;
        let auth_text = r#"{"azure":{"type":"api","key":"old","resource":"deployment"}}"#;
        write(&config, config_text);
        write(&auth, auth_text);
        let store = FakeStore::default();

        let error = save_provider_api_key(
            &store,
            std::slice::from_ref(&config),
            &auth,
            "azure",
            "new",
            &atomic,
        )
        .unwrap_err();

        assert!(error.contains("unsupported API auth record"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), auth_text);
        assert!(store.values.lock().unwrap().is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn secure_import_writes_only_placeholders_and_sanitized_oauth() {
        let dir = root("secure-import");
        let config = dir.join("opencode.jsonc");
        let auth = dir.join("auth.json");
        let imported = r#"{"acme":{"type":"api","key":"import-secret"},"oauth":{"type":"oauth","refresh":"keep","nested":{"n":1}}}"#;
        write(&config, r#"{"theme":"keep"}"#);
        write(&auth, r#"{"old":{"type":"oauth","refresh":"replace"}}"#);
        let store = FakeStore::default();

        import_auth_secure(
            &store,
            std::slice::from_ref(&config),
            &auth,
            imported,
            &atomic,
        )
        .unwrap();

        let config_value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(config_value["theme"], "keep");
        assert_eq!(
            config_value["provider"]["acme"]["options"]["apiKey"],
            provider_placeholder("acme").unwrap()
        );
        let auth_text = std::fs::read_to_string(&auth).unwrap();
        assert!(!auth_text.contains("import-secret"));
        let auth_value: Value = serde_json::from_str(&auth_text).unwrap();
        assert!(auth_value.get("acme").is_none());
        assert_eq!(
            auth_value["oauth"],
            json!({"type":"oauth","refresh":"keep","nested":{"n":1}})
        );
        assert_eq!(
            store.values.lock().unwrap().get("acme").map(String::as_str),
            Some("import-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn secure_import_keychain_failure_leaves_config_and_destination_untouched() {
        let dir = root("secure-import-save-failure");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text = r#"{"theme":"keep"}"#;
        let destination_text = r#"{"old":{"type":"oauth","refresh":"keep"}}"#;
        let imported = r#"{"acme":{"type":"api","key":"never-write-me"}}"#;
        write(&config, config_text);
        write(&auth, destination_text);
        let store = FakeStore {
            fail_set: true,
            ..Default::default()
        };

        assert!(import_auth_secure(
            &store,
            std::slice::from_ref(&config),
            &auth,
            imported,
            &atomic,
        )
        .is_err());
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), destination_text);
        assert!(store.values.lock().unwrap().is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn failed_login_rollback_removes_only_the_target_api_record() {
        let dir = root("auth-rollback");
        let auth = dir.join("auth.json");
        write(
            &auth,
            r#"{"azure/":{"type":"api","key":"secret","resource":"deployment"},"oauth":{"type":"oauth","refresh":"keep"}}"#,
        );

        assert!(rollback_provider_api_auth(&auth, "azure", &atomic).unwrap());
        let value: Value = serde_json::from_str(&std::fs::read_to_string(&auth).unwrap()).unwrap();
        assert!(value.get("azure/").is_none());
        assert_eq!(value["oauth"]["refresh"], "keep");
        assert!(!rollback_provider_api_auth(&auth, "oauth", &atomic).unwrap());

        write(
            &auth,
            r#"{"simple":{"type":"api","key":"fresh"},"oauth":{"type":"oauth","refresh":"keep"}}"#,
        );
        assert!(rollback_provider_api_auth(&auth, "simple", &atomic).unwrap());
        let value: Value = serde_json::from_str(&std::fs::read_to_string(&auth).unwrap()).unwrap();
        assert!(value.get("simple").is_none());
        assert_eq!(value["oauth"]["refresh"], "keep");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn file_reference_is_external_and_never_copied_to_the_keychain() {
        let dir = root("file-reference");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let config_text =
            r#"{"provider":{"acme":{"options":{"apiKey":"{file:~/.secrets/acme}"}}}}"#;
        write(&config, config_text);
        let store = FakeStore::default();

        let env =
            migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic).unwrap();
        assert!(env.is_empty());
        assert!(store.values.lock().unwrap().is_empty());
        assert_eq!(std::fs::read_to_string(&config).unwrap(), config_text);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn trailing_slash_provider_ids_share_one_normalized_credential() {
        let dir = root("trailing-slash");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        write(
            &config,
            r#"{"provider":{"acme/":{"options":{"apiKey":"same-secret"}}}}"#,
        );
        write(&auth, r#"{"acme/":{"type":"api","key":"same-secret"}}"#);
        let store = FakeStore::default();

        let env =
            migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic).unwrap();
        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(
            value["provider"]["acme/"]["options"]["apiKey"],
            provider_placeholder("acme").unwrap()
        );
        assert_eq!(env[0].0, provider_env_name("acme").unwrap());
        assert_eq!(env[0].1, "same-secret");
        assert_eq!(
            store.values.lock().unwrap().get("acme").map(String::as_str),
            Some("same-secret")
        );
        let auth_value: Value =
            serde_json::from_str(&std::fs::read_to_string(&auth).unwrap()).unwrap();
        assert!(auth_value.get("acme/").is_none());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn auth_only_api_key_creates_a_provider_placeholder() {
        let dir = root("auth-only");
        let config = dir.join("opencode.jsonc");
        let auth = dir.join("auth.json");
        write(&config, r#"{"theme":"keep"}"#);
        write(&auth, r#"{"acme":{"type":"api","key":"auth-secret"}}"#);
        let store = FakeStore::default();

        migrate_and_collect_env(&store, std::slice::from_ref(&config), &auth, &atomic).unwrap();
        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(value["theme"], "keep");
        assert_eq!(
            value["provider"]["acme"]["options"]["apiKey"],
            provider_placeholder("acme").unwrap()
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn both_config_files_are_scrubbed_after_all_conflicts_are_checked() {
        let dir = root("both-configs");
        let jsonc = dir.join("opencode.jsonc");
        let json = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        write(
            &jsonc,
            r#"{"provider":{"acme":{"name":"first","options":{"apiKey":"same"}}}}"#,
        );
        write(
            &json,
            r#"{"provider":{"acme/":{"name":"second","options":{"apiKey":"same"}}}}"#,
        );
        let store = FakeStore::default();

        migrate_and_collect_env(&store, &[jsonc.clone(), json.clone()], &auth, &atomic).unwrap();
        for (path, key) in [(&jsonc, "acme"), (&json, "acme/")] {
            let value: Value =
                serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
            assert_eq!(
                value["provider"][key]["options"]["apiKey"],
                provider_placeholder("acme").unwrap()
            );
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn conflicting_config_files_fail_before_either_file_or_keychain_changes() {
        let dir = root("config-file-conflict");
        let jsonc = dir.join("opencode.jsonc");
        let json = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        let first = r#"{"provider":{"acme":{"options":{"apiKey":"one"}}}}"#;
        let second = r#"{"provider":{"acme/":{"options":{"apiKey":"two"}}}}"#;
        write(&jsonc, first);
        write(&json, second);
        let store = FakeStore::default();

        let error = migrate_and_collect_env(&store, &[jsonc.clone(), json.clone()], &auth, &atomic)
            .unwrap_err();

        assert!(error.contains("conflicting API keys across OpenCode config files"));
        assert_eq!(std::fs::read_to_string(&jsonc).unwrap(), first);
        assert_eq!(std::fs::read_to_string(&json).unwrap(), second);
        assert!(store.values.lock().unwrap().is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn removing_the_only_api_key_prunes_an_empty_provider_override() {
        let dir = root("prune-provider");
        let config = dir.join("opencode.json");
        let auth = dir.join("auth.json");
        write(
            &config,
            &json!({
                "provider": {"acme": {"options": {
                    "apiKey": provider_placeholder("acme").unwrap()
                }}},
                "theme": "keep"
            })
            .to_string(),
        );
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([("acme".to_string(), "secret".to_string())])),
            ..Default::default()
        };

        remove_provider_api_key(
            &store,
            std::slice::from_ref(&config),
            &auth,
            "acme/",
            false,
            &atomic,
        )
        .unwrap();
        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert!(value["provider"].get("acme").is_none());
        assert_eq!(value["theme"], "keep");
        assert!(store.values.lock().unwrap().get("acme").is_none());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_migration_scrubs_plaintext_and_preserves_non_secret_config() {
        let dir = root("connector-migrate");
        let config = dir.join("opencode.jsonc");
        let command = previous_managed_connector_command("materials-project");
        write(
            &config,
            &json!({
                "theme":"keep",
                "mcp":{
                    "materials-project":{
                        "type":"local",
                        "command":command,
                        "enabled":true,
                        "environment":{"MP_API_KEY":"material-secret"}
                    },
                    "papers":{"type":"local","command":["papers"]}
                }
            })
            .to_string(),
        );
        let store = FakeStore::default();
        let commands = managed_connector_commands();

        let env = migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &commands,
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let text = std::fs::read_to_string(&config).unwrap();
        assert!(!text.contains("material-secret"));
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(value["theme"], "keep");
        assert!(value["mcp"]["materials-project"]["environment"]
            .get("MP_API_KEY")
            .is_none());
        assert_eq!(
            value["mcp"]["materials-project"]["environment"][CONNECTOR_OWNER_ENV],
            CONNECTOR_OWNER_V1
        );
        assert_eq!(value["mcp"]["papers"]["command"][0], "papers");
        assert_eq!(
            store
                .values
                .lock()
                .unwrap()
                .get("materials-project")
                .map(String::as_str),
            Some("material-secret")
        );
        assert_eq!(env, Vec::<(String, String)>::new());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn legacy_shared_connector_is_rewritten_and_disabled_without_running_old_code() {
        let dir = root("connector-legacy-shared");
        let config = dir.join("opencode.json");
        write(
            &config,
            &json!({
                "mcp": {"fred": {
                    "type": "local",
                    "command": legacy_managed_connector_command("fred"),
                    "enabled": true,
                    "environment": {"FRED_API_KEY": "legacy-secret"}
                }}
            })
            .to_string(),
        );
        let store = FakeStore::default();

        let env = migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let text = std::fs::read_to_string(&config).unwrap();
        assert!(!text.contains("legacy-secret"));
        assert!(!text.contains("legacy-shared"));
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            value["mcp"]["fred"]["command"],
            json!(managed_connector_command("fred"))
        );
        assert_eq!(value["mcp"]["fred"]["enabled"], false);
        assert!(value["mcp"]["fred"]["environment"]
            .get("FRED_API_KEY")
            .is_none());
        assert_eq!(
            value["mcp"]["fred"]["environment"][CONNECTOR_OWNER_ENV],
            CONNECTOR_OWNER_V1
        );
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("legacy-secret")
        );
        assert!(env.is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn previous_dedicated_connector_is_rewritten_to_the_broker_launcher() {
        let dir = root("connector-previous-dedicated");
        let config = dir.join("opencode.json");
        write(
            &config,
            &json!({
                "mcp": {"fred": {
                    "type": "local",
                    "command": previous_managed_connector_command("fred"),
                    "enabled": true,
                    "environment": {
                        "FRED_API_KEY": connector_placeholder("fred").unwrap(),
                        (CONNECTOR_OWNER_ENV): CONNECTOR_OWNER_V1
                    }
                }}
            })
            .to_string(),
        );
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "fred".to_string(),
                "stored-secret".to_string(),
            )])),
            ..Default::default()
        };

        migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let text = std::fs::read_to_string(&config).unwrap();
        assert!(!text.contains("SPARK_OPENCODE_CONNECTOR_KEY_"));
        assert!(!text.contains("previous-dedicated"));
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            value["mcp"]["fred"],
            json!({
                "type":"local",
                "command":managed_connector_command("fred"),
                "enabled":true,
                "environment":{(CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1}
            })
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn tampered_previous_dedicated_connector_is_scrubbed_and_disabled() {
        let dir = root("connector-tampered-previous-dedicated");
        let config = dir.join("opencode.json");
        write(
            &config,
            &json!({
                "mcp": {"fred": {
                    "type": "local",
                    "command": previous_managed_connector_command("fred"),
                    "enabled": true,
                    "cwd": "/tmp/attacker",
                    "environment": {
                        "FRED_API_KEY": "previous-secret",
                        "LD_PRELOAD": "/tmp/attacker.dylib"
                    }
                }}
            })
            .to_string(),
        );
        let store = FakeStore::default();

        migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let text = std::fs::read_to_string(&config).unwrap();
        assert!(!text.contains("previous-secret"));
        assert!(!text.contains("previous-dedicated"));
        assert!(!text.contains("LD_PRELOAD"));
        assert!(!text.contains("attacker"));
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(value["mcp"]["fred"]["enabled"], false);
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("previous-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn tampered_legacy_shared_connector_is_quarantined_and_scrubbed() {
        let dir = root("connector-tampered-legacy-shared");
        let config = dir.join("opencode.json");
        write(
            &config,
            &json!({
                "mcp": {"fred": {
                    "type": "local",
                    "command": legacy_managed_connector_command("fred"),
                    "enabled": true,
                    "cwd": "/tmp/attacker",
                    "environment": {
                        "FRED_API_KEY": "legacy-secret",
                        "LD_PRELOAD": "/tmp/attacker.dylib"
                    }
                }}
            })
            .to_string(),
        );
        let store = FakeStore::default();

        migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let text = std::fs::read_to_string(&config).unwrap();
        assert!(!text.contains("legacy-secret"));
        assert!(!text.contains("LD_PRELOAD"));
        assert!(!text.contains("attacker"));
        assert!(!text.contains("legacy-shared"));
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            value["mcp"]["fred"],
            json!({
                "type":"local",
                "command":managed_connector_command("fred"),
                "enabled":false,
                "environment":{(CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1}
            })
        );
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("legacy-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn relocated_owned_relay_is_rewritten_to_the_current_socket() {
        let dir = root("connector-relocated-relay");
        let config = dir.join("opencode.json");
        let mut relocated = managed_connector_command("fred");
        relocated[2] = "/private/tmp/spark-mcp-fedcba9876543210/fred.sock".to_string();
        write(
            &config,
            &json!({
                "mcp":{"fred":{
                    "type":"local",
                    "command":relocated,
                    "enabled":true,
                    "environment":{(CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1}
                }}
            })
            .to_string(),
        );
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "fred".to_string(),
                "stored-secret".to_string(),
            )])),
            ..Default::default()
        };

        let env = migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(
            value["mcp"]["fred"]["command"],
            json!(managed_connector_command("fred"))
        );
        assert_eq!(value["mcp"]["fred"]["enabled"], true);
        assert!(env.is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn security_gate_canonicalizes_and_disables_owned_keyed_connectors() {
        let commands = managed_connector_commands();
        let config = json!({
            "mcp": {
                "fred": {
                    "type": "local",
                    "command": managed_connector_command("fred"),
                    "enabled": true,
                    "environment": {(CONNECTOR_OWNER_ENV): CONNECTOR_OWNER_V1}
                },
                "papers": {"type": "local", "command": ["papers"], "enabled": true}
            }
        });

        let plan = plan_security_gated_connector_migration(
            &config.to_string(),
            &commands,
            &legacy_managed_connector_commands(),
        )
        .unwrap();

        assert!(plan.config_changed);
        assert_eq!(plan.config["mcp"]["fred"]["enabled"], false);
        assert_eq!(
            plan.config["mcp"]["fred"]["command"],
            json!(managed_connector_command("fred"))
        );
        assert_eq!(plan.config["mcp"]["papers"]["enabled"], true);
    }

    #[test]
    fn disabled_effective_connector_is_scrubbed_without_secret_injection() {
        let dir = root("connector-disabled");
        let config = dir.join("opencode.json");
        write(
            &config,
            &json!({
                "mcp": {"fred": {
                    "type": "local",
                    "command": previous_managed_connector_command("fred"),
                    "enabled": false,
                    "environment": {"FRED_API_KEY": "disabled-secret"}
                }}
            })
            .to_string(),
        );
        let store = FakeStore::default();

        let env = migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let text = std::fs::read_to_string(&config).unwrap();
        assert!(!text.contains("disabled-secret"));
        assert!(env.is_empty());
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("disabled-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn same_name_nonmanaged_connectors_are_ignored_without_inspecting_their_keys() {
        let commands = managed_connector_commands();
        for connector in [
            json!({
                "type":"local",
                "command":["/user/bin/fred"],
                "environment":{"FRED_API_KEY":"literal-owned-by-user"}
            }),
            json!({
                "type":"local",
                "command":["/user/bin/fred"],
                "environment":{"FRED_API_KEY":"{env:USER_FRED_KEY}"}
            }),
            json!({"type":"local","command":["/user/bin/fred"]}),
        ] {
            let text = json!({"mcp":{"fred":connector}}).to_string();
            let plan =
                plan_connector_migration(&text, &commands, &legacy_managed_connector_commands())
                    .unwrap();
            assert!(!plan.config_changed);
            assert!(plan.secrets_to_save.is_empty());
            assert!(plan.required_connectors.is_empty());
        }
    }

    #[test]
    fn exact_command_without_a_spark_claim_ignores_missing_empty_and_external_keys() {
        let commands = managed_connector_commands();
        let command = managed_connector_command("fred");
        for connector in [
            json!({"type":"local","command":command.clone()}),
            json!({
                "type":"local","command":command.clone(),
                "environment":{"FRED_API_KEY":"user-owned-literal"}
            }),
            json!({
                "type":"local","command":command.clone(),
                "environment":{"FRED_API_KEY":"   "}
            }),
            json!({
                "type":"local","command":command,
                "environment":{"FRED_API_KEY":"{env:USER_FRED_KEY}"}
            }),
        ] {
            let text = json!({"mcp":{"fred":connector}}).to_string();
            let plan =
                plan_connector_migration(&text, &commands, &legacy_managed_connector_commands())
                    .unwrap();
            assert!(!plan.config_changed);
            assert!(plan.secrets_to_save.is_empty());
            assert!(plan.required_connectors.is_empty());
        }
    }

    #[test]
    fn legacy_literal_requires_a_strict_execution_shape() {
        let commands = managed_connector_commands();
        let command = managed_connector_command("fred");
        for connector in [
            json!({
                "type":"local","command":command.clone(),"unknown":"value",
                "environment":{"FRED_API_KEY":"literal"}
            }),
            json!({
                "type":"local","command":command.clone(),
                "environment":{"FRED_API_KEY":"literal","LD_PRELOAD":"/tmp/inject.dylib"}
            }),
        ] {
            let text = json!({"mcp":{"fred":connector}}).to_string();
            let plan =
                plan_connector_migration(&text, &commands, &legacy_managed_connector_commands())
                    .unwrap();
            assert!(!plan.config_changed);
            assert!(plan.secrets_to_save.is_empty());
            assert!(plan.required_connectors.is_empty());
        }

        let marked_extra_environment = json!({"mcp":{"fred":{
            "type":"local","command":command.clone(),
            "environment":{
                "FRED_API_KEY":"literal",
                (CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1,
                "PATH":"/tmp/attacker"
            }
        }}})
        .to_string();
        let error = plan_connector_migration(
            &marked_extra_environment,
            &commands,
            &legacy_managed_connector_commands(),
        )
        .unwrap_err();
        assert!(error.contains("unsupported execution fields"));

        let placeholder_extra_top_level = json!({"mcp":{"fred":{
            "type":"local","command":command,"args":["--unexpected"],
            "environment":{"FRED_API_KEY":connector_placeholder("fred").unwrap()}
        }}})
        .to_string();
        let error = plan_connector_migration(
            &placeholder_extra_top_level,
            &commands,
            &legacy_managed_connector_commands(),
        )
        .unwrap_err();
        assert!(error.contains("unsupported execution fields"));
    }

    #[test]
    fn missing_connector_credential_disables_only_that_connector() {
        let dir = root("connector-missing");
        let config = dir.join("opencode.json");
        let text = json!({
            "mcp": {"fred": {
                "type": "local",
                "command": managed_connector_command("fred"),
                "environment": {"FRED_API_KEY": connector_placeholder("fred").unwrap()}
            }}
        })
        .to_string();
        write(&config, &text);

        let env = migrate_and_collect_connector_env(
            &FakeStore::default(),
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        assert!(env.is_empty());
        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(value["mcp"]["fred"]["enabled"], false);
        assert!(value["mcp"]["fred"]["environment"]
            .get("FRED_API_KEY")
            .is_none());
        assert_eq!(
            value["mcp"]["fred"]["environment"][CONNECTOR_OWNER_ENV],
            CONNECTOR_OWNER_V1
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn legacy_exact_placeholder_gains_owner_marker_after_keychain_preflight() {
        let dir = root("connector-marker-upgrade");
        let config = dir.join("opencode.json");
        write(
            &config,
            &json!({
                "mcp":{"fred":{
                    "type":"local",
                    "command":managed_connector_command("fred"),
                    "environment":{"FRED_API_KEY":connector_placeholder("fred").unwrap()}
                }}
            })
            .to_string(),
        );
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "fred".to_string(),
                "stored-secret".to_string(),
            )])),
            ..Default::default()
        };

        let env = migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&config),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap();

        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(
            value["mcp"]["fred"]["environment"][CONNECTOR_OWNER_ENV],
            CONNECTOR_OWNER_V1
        );
        assert!(env.is_empty());
        assert!(value["mcp"]["fred"]["environment"]
            .get("FRED_API_KEY")
            .is_none());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_migration_rejects_keychain_and_cross_file_conflicts() {
        let dir = root("connector-conflicts");
        let first = dir.join("opencode.jsonc");
        let second = dir.join("opencode.json");
        let first_text = json!({"mcp":{"fred":{
            "type":"local","command":previous_managed_connector_command("fred"),
            "environment":{"FRED_API_KEY":"one"}
        }}})
        .to_string();
        let second_text = json!({"mcp":{"fred":{
            "type":"local","command":previous_managed_connector_command("fred"),
            "environment":{"FRED_API_KEY":"two"}
        }}})
        .to_string();
        write(&first, &first_text);
        write(&second, &second_text);
        let store = FakeStore::default();

        let error = migrate_and_collect_connector_env(
            &store,
            &[first.clone(), second.clone()],
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap_err();
        assert!(error.contains("conflicting API keys across OpenCode config files"));
        assert_eq!(std::fs::read_to_string(&first).unwrap(), first_text);
        assert_eq!(std::fs::read_to_string(&second).unwrap(), second_text);
        assert!(store.values.lock().unwrap().is_empty());

        std::fs::remove_file(&second).unwrap();
        store
            .values
            .lock()
            .unwrap()
            .insert("fred".to_string(), "credential-manager-value".to_string());
        let error = migrate_and_collect_connector_env(
            &store,
            std::slice::from_ref(&first),
            &managed_connector_commands(),
            &legacy_managed_connector_commands(),
            &atomic,
        )
        .unwrap_err();
        assert!(error.contains("system credential manager"));
        assert_eq!(std::fs::read_to_string(&first).unwrap(), first_text);
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("credential-manager-value")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_external_or_mismatched_references_are_never_adopted() {
        let commands = managed_connector_commands();
        let external = json!({"mcp":{"fred":{
            "type":"local","command":managed_connector_command("fred"),
            "environment":{
                "FRED_API_KEY":"{env:USER_FRED_KEY}",
                (CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1
            }
        }}})
        .to_string();
        let error =
            plan_connector_migration(&external, &commands, &legacy_managed_connector_commands())
                .unwrap_err();
        assert!(error.contains("external API-key reference"));

        let mismatched = json!({
            "mcp": {"fred": {
                "type": "local",
                "command": managed_connector_command("fred"),
                "environment": {"FRED_API_KEY": connector_placeholder("materials-project").unwrap()}
            }}
        })
        .to_string();
        let error =
            plan_connector_migration(&mismatched, &commands, &legacy_managed_connector_commands())
                .unwrap_err();
        assert!(error.contains("mismatched Spark credential reference"));

        let marked_wrong_command = json!({"mcp":{"fred":{
            "type":"local","command":["/user/bin/fred"],
            "environment":{
                "FRED_API_KEY":"literal",
                (CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1
            }
        }}})
        .to_string();
        let error = plan_connector_migration(
            &marked_wrong_command,
            &commands,
            &legacy_managed_connector_commands(),
        )
        .unwrap_err();
        assert!(error.contains("mismatched managed command"));

        let wrong_marker = json!({"mcp":{"fred":{
            "type":"local","command":managed_connector_command("fred"),
            "environment":{
                "FRED_API_KEY":"literal",
                (CONNECTOR_OWNER_ENV):"spark-agent/v2"
            }
        }}})
        .to_string();
        let error = plan_connector_migration(
            &wrong_marker,
            &commands,
            &legacy_managed_connector_commands(),
        )
        .unwrap_err();
        assert!(error.contains("mismatched Spark owner marker"));

        let marked_missing_key = json!({"mcp":{"fred":{
            "type":"local","command":managed_connector_command("fred"),
            "environment":{(CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1}
        }}})
        .to_string();
        let plan = plan_connector_migration(
            &marked_missing_key,
            &commands,
            &legacy_managed_connector_commands(),
        )
        .unwrap();
        assert!(plan.required_connectors.contains("fred"));
        assert!(plan.secrets_to_save.is_empty());
    }

    #[test]
    fn connector_secure_save_writes_only_broker_ownership_and_preserves_other_mcp() {
        let dir = root("connector-save");
        let config = dir.join("opencode.json");
        write(
            &config,
            r#"{"theme":"keep","mcp":{"papers":{"type":"local","command":["papers"]}}}"#,
        );
        let store = FakeStore::default();
        let command = managed_connector_command("fred");

        save_connector_api_key(
            &store,
            std::slice::from_ref(&config),
            "fred",
            "  fresh-secret  ",
            &command,
            &atomic,
        )
        .unwrap();

        let text = std::fs::read_to_string(&config).unwrap();
        assert!(!text.contains("fresh-secret"));
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(value["theme"], "keep");
        assert_eq!(value["mcp"]["papers"]["command"][0], "papers");
        assert!(value["mcp"]["fred"]["environment"]
            .get("FRED_API_KEY")
            .is_none());
        assert_eq!(
            value["mcp"]["fred"]["environment"][CONNECTOR_OWNER_ENV],
            CONNECTOR_OWNER_V1
        );
        assert_eq!(
            value["mcp"]["fred"],
            json!({
                "type":"local",
                "command":command,
                "enabled":true,
                "environment":{
                    (CONNECTOR_OWNER_ENV):CONNECTOR_OWNER_V1
                }
            })
        );
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("fresh-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_save_rejects_non_native_commands_and_rolls_back_write_failures() {
        let dir = root("connector-save-rollback");
        let config = dir.join("opencode.json");
        let original = r#"{"theme":"keep"}"#;
        write(&config, original);
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "fred".to_string(),
                "previous-secret".to_string(),
            )])),
            ..Default::default()
        };
        let error = save_connector_api_key(
            &store,
            std::slice::from_ref(&config),
            "fred",
            "renderer-secret",
            &["/tmp/arbitrary-command".to_string()],
            &atomic,
        )
        .unwrap_err();
        assert!(error.contains("native relay"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), original);

        let fail_write = |_path: &Path, _bytes: &[u8]| -> Result<(), String> {
            Err("fake connector config write failure".to_string())
        };
        let command = managed_connector_command("fred");
        let error = save_connector_api_key(
            &store,
            std::slice::from_ref(&config),
            "fred",
            "replacement-secret",
            &command,
            &fail_write,
        )
        .unwrap_err();
        assert!(error.contains("fake connector config write failure"));
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("previous-secret")
        );
        assert_eq!(std::fs::read_to_string(&config).unwrap(), original);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_multi_config_failure_restores_secondary_and_prior_credential() {
        let dir = root("connector-multi-rollback");
        let effective = dir.join("opencode.jsonc");
        let secondary = dir.join("opencode.json");
        let effective_text = r#"{"theme":"effective"}"#;
        let secondary_text =
            r#"{"theme":"legacy","mcp":{"fred":{"type":"local","command":["/user/bin/fred"]}}}"#;
        write(&effective, effective_text);
        write(&secondary, secondary_text);
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "fred".to_string(),
                "previous-secret".to_string(),
            )])),
            ..Default::default()
        };
        let effective_for_writer = effective.clone();
        let fail_effective = move |path: &Path, bytes: &[u8]| -> Result<(), String> {
            if path == effective_for_writer {
                Err("effective write failed".to_string())
            } else {
                atomic(path, bytes)
            }
        };

        let error = save_connector_api_key(
            &store,
            &[effective.clone(), secondary.clone()],
            "fred",
            "replacement-secret",
            &managed_connector_command("fred"),
            &fail_effective,
        )
        .unwrap_err();

        assert!(error.contains("effective write failed"));
        assert_eq!(std::fs::read_to_string(&effective).unwrap(), effective_text);
        assert_eq!(std::fs::read_to_string(&secondary).unwrap(), secondary_text);
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("previous-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_multi_config_error_combines_rollback_failure() {
        let dir = root("connector-multi-rollback-error");
        let effective = dir.join("opencode.jsonc");
        let secondary = dir.join("opencode.json");
        write(&effective, r#"{"theme":"effective"}"#);
        write(
            &secondary,
            r#"{"mcp":{"fred":{"type":"local","command":["old"]}}}"#,
        );
        let secondary_writes = Mutex::new(0_u8);
        let effective_for_writer = effective.clone();
        let fail_write_and_rollback = move |path: &Path, bytes: &[u8]| -> Result<(), String> {
            if path == effective_for_writer {
                return Err("effective write failed".to_string());
            }
            let mut writes = secondary_writes.lock().unwrap();
            *writes += 1;
            if *writes > 1 {
                Err("secondary rollback failed".to_string())
            } else {
                atomic(path, bytes)
            }
        };

        let error = save_connector_api_key(
            &FakeStore::default(),
            &[effective, secondary],
            "fred",
            "secret",
            &managed_connector_command("fred"),
            &fail_write_and_rollback,
        )
        .unwrap_err();

        assert!(error.contains("effective write failed"));
        assert!(error.contains("secondary rollback failed"));
        assert!(error.contains("failed to roll back OpenCode config transaction"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_removal_drops_references_before_deleting_the_credential() {
        let dir = root("connector-remove");
        let config = dir.join("opencode.json");
        write(
            &config,
            &json!({
                "mcp": {
                    "fred": {
                        "type":"local",
                        "command":managed_connector_command("fred"),
                        "environment":{"FRED_API_KEY":connector_placeholder("fred").unwrap()}
                    },
                    "papers":{"type":"local","command":["papers"]}
                }
            })
            .to_string(),
        );
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "fred".to_string(),
                "stale-secret".to_string(),
            )])),
            fail_delete: true,
            ..Default::default()
        };

        let error = remove_connector_api_key(
            &store,
            std::slice::from_ref(&config),
            "fred",
            &managed_connector_command("fred"),
            &atomic,
        )
        .unwrap_err();
        assert!(error.contains("fake delete failure"));
        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert!(value["mcp"].get("fred").is_none());
        assert_eq!(value["mcp"]["papers"]["command"][0], "papers");
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("stale-secret")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_removal_does_not_delete_credentials_for_unowned_same_name_config() {
        let dir = root("connector-remove-unowned");
        let config = dir.join("opencode.json");
        write(
            &config,
            r#"{"mcp":{"fred":{"type":"local","command":["/user/bin/fred"],"environment":{"FRED_API_KEY":"user-secret"}}}}"#,
        );
        let store = FakeStore {
            values: Mutex::new(BTreeMap::from([(
                "fred".to_string(),
                "unrelated-stale-item".to_string(),
            )])),
            ..Default::default()
        };

        remove_connector_api_key(
            &store,
            std::slice::from_ref(&config),
            "fred",
            &managed_connector_command("fred"),
            &atomic,
        )
        .unwrap();

        let value: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert!(value["mcp"].get("fred").is_none());
        assert_eq!(
            store.values.lock().unwrap().get("fred").map(String::as_str),
            Some("unrelated-stale-item")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn connector_env_names_are_stable_opaque_and_allowlisted() {
        let materials = connector_env_name("materials-project").unwrap();
        assert_eq!(materials.len(), CONNECTOR_ENV_PREFIX.len() + 64);
        assert!(!materials.contains("materials"));
        assert_eq!(
            materials,
            "SPARK_OPENCODE_CONNECTOR_KEY_9F78F7F71C51A41C1BEF19140481CD55FF52F7915F76980E4A30F78EA1CF7FA6"
        );
        assert_eq!(
            connector_env_name("fred").unwrap(),
            "SPARK_OPENCODE_CONNECTOR_KEY_F62CF88F169CDEF40A5A3A99294DC9BC64FCD510E2C68A07C0ED490FD6622C68"
        );
        assert!(connector_credential_spec("fred").is_ok());
        assert!(connector_credential_spec("paper-search").is_err());
        assert!(save_connector_api_key(
            &FakeStore::default(),
            &[root("unsupported").join("opencode.json")],
            "paper-search",
            "secret",
            &["/managed/bin/paper-search".to_string()],
            &atomic,
        )
        .is_err());
    }

    #[test]
    fn full_hash_env_name_is_stable_and_contains_no_provider_text() {
        let name = provider_env_name("private-provider").unwrap();
        assert!(name.starts_with(ENV_PREFIX));
        assert_eq!(name.len(), ENV_PREFIX.len() + 64);
        assert!(!name.contains("private-provider"));
        assert_eq!(
            name,
            "SPARK_OPENCODE_KEY_C215C146C58EAC7605E1047640511456CCE7D6DD74301529402D136FE70EDDA3"
        );
        assert_eq!(name, provider_env_name("private-provider/").unwrap());
    }
}
