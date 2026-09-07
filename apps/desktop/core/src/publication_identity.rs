use crate::{
    audio_resource::{LocalAudioCopyReceipt, MAX_LOCAL_AUDIO_FILE_BYTES},
    runtime_core::{is_valid_project_id, AUDIO_EXTENSIONS},
};
use serde::{Deserialize, Serialize};

const LOCAL_AUDIO_PUBLICATION_IDENTITY_ERROR: &str =
    "Could not prepare the local project workspace.";

/// Path-free native identity for one verified app-owned local-audio publication.
///
/// This value is suitable for Project Persistence handoff because it names only
/// a BandScope-owned artifact and carries the exact native size/digest evidence
/// produced by Resource Admission. It never contains an external or absolute
/// filesystem path.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LocalAudioPublicationIdentity {
    /// Locally minted BandScope project id that owns the publication.
    pub project_id: String,
    /// Deterministic app-owned artifact name within that project.
    pub artifact_name: String,
    /// Canonical lowercase admitted audio extension.
    pub extension: String,
    /// Exact number of bytes in the verified publication.
    pub file_size_bytes: u64,
    /// Lowercase SHA-256 of the exact verified publication bytes.
    pub content_sha256: String,
}

fn is_lowercase_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// Build the durable path-free identity for verified local-audio publication evidence.
///
/// Security Notes: callers must supply a project id minted under BandScope's
/// existing project-id grammar and the canonical lowercase extension that was
/// admitted by Resource Admission. The receipt must come from the verified
/// publication path, not renderer input. Invalid ids, extensions, sizes, or
/// digest encodings fail closed with the bounded project-workspace diagnosis.
pub fn build_local_audio_publication_identity(
    project_id: &str,
    extension: &str,
    receipt: &LocalAudioCopyReceipt,
) -> Result<LocalAudioPublicationIdentity, String> {
    if !is_valid_project_id(project_id)
        || !AUDIO_EXTENSIONS.contains(&extension)
        || extension.bytes().any(|byte| byte.is_ascii_uppercase())
        || receipt.file_size_bytes == 0
        || receipt.file_size_bytes > MAX_LOCAL_AUDIO_FILE_BYTES
        || !is_lowercase_sha256(&receipt.content_sha256)
    {
        return Err(LOCAL_AUDIO_PUBLICATION_IDENTITY_ERROR.to_string());
    }

    Ok(LocalAudioPublicationIdentity {
        project_id: project_id.to_string(),
        artifact_name: format!("source.{extension}"),
        extension: extension.to_string(),
        file_size_bytes: receipt.file_size_bytes,
        content_sha256: receipt.content_sha256.clone(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lowercase_sha256_requires_exact_canonical_encoding() {
        assert!(is_lowercase_sha256(
            "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
        ));
        assert!(!is_lowercase_sha256(&"a".repeat(63)));
        assert!(!is_lowercase_sha256(&"a".repeat(65)));
        assert!(!is_lowercase_sha256(&"A".repeat(64)));
        assert!(!is_lowercase_sha256(&"g".repeat(64)));
    }
}
