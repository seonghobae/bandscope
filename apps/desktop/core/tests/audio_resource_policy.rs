use bandscope_desktop_core::{validate_local_audio_file_size, MAX_LOCAL_AUDIO_FILE_BYTES};

#[test]
fn local_audio_size_policy_accepts_the_exact_native_bootstrap_ceiling() {
    assert_eq!(
        validate_local_audio_file_size(MAX_LOCAL_AUDIO_FILE_BYTES),
        Ok(MAX_LOCAL_AUDIO_FILE_BYTES)
    );
}

#[test]
fn local_audio_size_policy_rejects_an_empty_native_bootstrap_source() {
    assert_eq!(
        validate_local_audio_file_size(0),
        Err("Could not read the selected audio file.".to_string())
    );
}

#[test]
fn local_audio_size_policy_rejects_a_native_source_above_the_canonical_ceiling() {
    assert_eq!(
        validate_local_audio_file_size(MAX_LOCAL_AUDIO_FILE_BYTES + 1),
        Err("Choose a shorter or smaller song file to start analysis.".to_string())
    );
}
