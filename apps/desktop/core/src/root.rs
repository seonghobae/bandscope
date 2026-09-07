//! Pure, GUI-independent logic for the BandScope desktop application.
//!
//! The historical desktop-core implementation remains in `lib.rs` as the
//! compatibility module while bounded resource boundaries are isolated in
//! auditable modules. Public symbols are re-exported so downstream callers keep
//! the same crate-root API.

#[path = "lib.rs"]
mod runtime_core;
mod audio_resource;
mod content_sha256;
mod publication_identity;
mod score_pdf;

pub use audio_resource::{
    copy_bounded_local_audio, copy_bounded_local_audio_with_receipt,
    validate_local_audio_file_size, verify_local_audio_publication_receipt,
    LocalAudioCopyReceipt, MAX_LOCAL_AUDIO_FILE_BYTES,
};
pub use content_sha256::sha256_hex_reader;
pub use publication_identity::{
    build_local_audio_publication_identity, LocalAudioPublicationIdentity,
};
pub use runtime_core::*;
pub use score_pdf::read_validated_score_pdf;
