use bandscope_desktop_core::{validate_local_audio_file_size, MAX_LOCAL_AUDIO_FILE_BYTES};

#[test]
fn oversized_local_audio_names_the_next_rehearsal_action() {
    assert_eq!(
        validate_local_audio_file_size(MAX_LOCAL_AUDIO_FILE_BYTES + 1),
        Err("Choose a shorter or smaller song file to start analysis.".to_string())
    );
}
