//! Provider API-key custody for the bundled OpenCode runtime.
//!
//! API keys live in the OS credential manager. OpenCode config contains only a
//! provider-specific `{env:...}` reference, and the sidecar receives the value
//! in its child environment. OAuth records remain owned by OpenCode.

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

const SERVICE: &str = "io.github.shawliu998.sparkagent.opencode-provider";
const ENV_PREFIX: &str = "SPARK_OPENCODE_KEY_";
type AtomicWriter = dyn Fn(&Path, &[u8]) -> Result<(), String>;

pub(crate) trait CredentialStore {
    fn get(&self, provider_id: &str) -> Result<Option<String>, String>;
    fn set(&self, provider_id: &str, secret: &str) -> Result<(), String>;
    fn delete(&self, provider_id: &str) -> Result<(), String>;
}

pub(crate) struct SystemCredentialStore;

impl SystemCredentialStore {
    fn entry(provider_id: &str) -> Result<keyring::Entry, String> {
        let provider_id = normalize_provider_id(provider_id)?;
        keyring::Entry::new(SERVICE, &provider_id).map_err(|_| {
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
