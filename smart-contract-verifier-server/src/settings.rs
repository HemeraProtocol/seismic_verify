use anyhow::anyhow;
use blockscout_service_launcher::{
    launcher::{ConfigSettings, MetricsSettings, ServerSettings},
    tracing::{JaegerSettings, TracingSettings},
};
use cron::Schedule;
use serde::Deserialize;
use serde_with::{serde_as, DisplayFromStr};
use smart_contract_verifier::{
    DEFAULT_ERA_SOLIDITY_COMPILER_LIST, DEFAULT_SOLIDITY_COMPILER_LIST, DEFAULT_SOURCIFY_HOST,
    DEFAULT_VYPER_COMPILER_LIST, DEFAULT_ZKSOLC_COMPILER_LIST,
};
use std::{
    num::{NonZeroU32, NonZeroUsize},
    path::PathBuf,
    str::FromStr,
};
use url::Url;

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub server: ServerSettings,
    pub solidity: SoliditySettings,
    pub vyper: VyperSettings,
    pub sourcify: SourcifySettings,
    pub zksync_solidity: ZksyncSoliditySettings,
    pub metrics: MetricsSettings,
    pub jaeger: JaegerSettings,
    pub tracing: TracingSettings,
    pub compilers: CompilersSettings,
}

#[serde_as]
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SoliditySettings {
    pub enabled: bool,
    pub compilers_dir: PathBuf,
    #[serde_as(as = "DisplayFromStr")]
    pub refresh_versions_schedule: Schedule,
    pub fetcher: FetcherSettings,
}

impl Default for SoliditySettings {
    fn default() -> Self {
        Self {
            enabled: true,
            compilers_dir: default_compilers_dir("solidity-compilers"),
            refresh_versions_schedule: schedule_every_hour(),
            fetcher: default_list_fetcher(DEFAULT_SOLIDITY_COMPILER_LIST),
        }
    }
}

#[serde_as]
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct VyperSettings {
    pub enabled: bool,
    pub compilers_dir: PathBuf,
    #[serde_as(as = "DisplayFromStr")]
    pub refresh_versions_schedule: Schedule,
    pub fetcher: FetcherSettings,
}

impl Default for VyperSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            compilers_dir: default_compilers_dir("vyper-compilers"),
            refresh_versions_schedule: schedule_every_hour(),
            fetcher: default_list_fetcher(DEFAULT_VYPER_COMPILER_LIST),
        }
    }
}

#[derive(Deserialize, Clone, PartialEq, Eq, Debug)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub enum FetcherSettings {
    List(ListFetcherSettings),
    S3(S3FetcherSettings),
}

#[derive(Deserialize, Clone, PartialEq, Eq, Debug)]
#[serde(deny_unknown_fields)]
pub struct ListFetcherSettings {
    pub list_url: Url,
}

#[derive(Deserialize, Default, Clone, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct S3FetcherSettings {
    pub access_key: Option<String>,
    pub secret_key: Option<String>,
    pub region: Option<String>,
    pub endpoint: Option<String>,
    pub bucket: String,
}

// Settings may be formatted by startup diagnostics; credentials must never appear.
impl std::fmt::Debug for S3FetcherSettings {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("S3FetcherSettings")
            .field("access_key", &self.access_key.as_ref().map(|_| "[REDACTED]"))
            .field("secret_key", &self.secret_key.as_ref().map(|_| "[REDACTED]"))
            .field("region", &self.region)
            .field("endpoint", &self.endpoint)
            .field("bucket", &self.bucket)
            .finish()
    }
}

#[cfg(test)]
mod credential_logging_tests {
    use super::*;

    #[test]
    fn s3_debug_redacts_credentials() {
        let secret = S3FetcherSettings {
            access_key: Some("test-sensitive-access-value".into()),
            secret_key: Some("test-sensitive-secret-value".into()),
            bucket: "compiler-test-bucket".into(),
            ..Default::default()
        };
        let mut settings = Settings::default();
        settings.solidity.fetcher = FetcherSettings::S3(secret);
        for output in [format!("{:?}", settings), format!("{:#?}", settings)] {
            assert!(!output.contains("test-sensitive-access-value"));
            assert!(!output.contains("test-sensitive-secret-value"));
            assert!(output.contains("[REDACTED]"));
            assert!(output.contains("compiler-test-bucket"));
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SourcifySettings {
    pub enabled: bool,
    pub api_url: Url,
    /// Number of attempts the server makes to Sourcify API.
    /// Should be at least one. Set to `3` by default.
    pub verification_attempts: NonZeroU32,
    pub request_timeout: u64,
}

impl Default for SourcifySettings {
    fn default() -> Self {
        Self {
            enabled: true,
            api_url: Url::try_from(DEFAULT_SOURCIFY_HOST).expect("valid url"),
            verification_attempts: NonZeroU32::new(3).expect("Is not zero"),
            request_timeout: 15,
        }
    }
}

#[serde_as]
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ZksyncSoliditySettings {
    pub enabled: bool,
    pub evm_compilers_dir: PathBuf,
    #[serde_as(as = "DisplayFromStr")]
    pub evm_refresh_versions_schedule: Schedule,
    pub evm_fetcher: FetcherSettings,
    pub era_evm_fetcher: FetcherSettings,
    pub zk_compilers_dir: PathBuf,
    #[serde_as(as = "DisplayFromStr")]
    pub zk_refresh_versions_schedule: Schedule,
    pub zk_fetcher: FetcherSettings,
}

impl Default for ZksyncSoliditySettings {
    fn default() -> Self {
        Self {
            enabled: true,
            evm_compilers_dir: default_compilers_dir("zksync-solc-compilers"),
            evm_refresh_versions_schedule: schedule_every_hour(),
            evm_fetcher: default_list_fetcher(DEFAULT_SOLIDITY_COMPILER_LIST),
            era_evm_fetcher: default_list_fetcher(DEFAULT_ERA_SOLIDITY_COMPILER_LIST),
            zk_compilers_dir: default_compilers_dir("zksync-zksolc-compilers"),
            zk_refresh_versions_schedule: schedule_every_hour(),
            zk_fetcher: default_list_fetcher(DEFAULT_ZKSOLC_COMPILER_LIST),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct CompilersSettings {
    pub max_threads: NonZeroUsize,
}

impl Default for CompilersSettings {
    fn default() -> Self {
        let max_threads = std::thread::available_parallelism().unwrap_or_else(|e| {
            tracing::warn!("cannot get number of CPU cores: {}", e);
            NonZeroUsize::new(8).unwrap()
        });
        Self { max_threads }
    }
}

impl ConfigSettings for Settings {
    const SERVICE_NAME: &'static str = "SMART_CONTRACT_VERIFIER";

    fn validate(&self) -> anyhow::Result<()> {
        // Validate s3 fetcher
        if let FetcherSettings::S3(settings) = &self.solidity.fetcher {
            if settings.region.is_none() && settings.endpoint.is_none() {
                return Err(anyhow!("for s3 fetcher settings at least one of `region` or `endpoint` should be defined"));
            }
        };

        Ok(())
    }
}

fn default_compilers_dir<P: AsRef<std::path::Path>>(path: P) -> PathBuf {
    let mut compilers_dir = std::env::temp_dir();
    compilers_dir.push(path);
    compilers_dir
}

fn default_list_fetcher(list_url: &str) -> FetcherSettings {
    FetcherSettings::List(ListFetcherSettings {
        list_url: Url::try_from(list_url).expect("invalid default list.json url"),
    })
}

fn schedule_every_hour() -> Schedule {
    Schedule::from_str("0 0 * * * * *").unwrap()
}
