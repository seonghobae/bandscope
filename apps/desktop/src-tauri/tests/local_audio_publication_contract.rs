#[test]
fn local_audio_materializer_consumes_publication_bound_receipt() {
    let source = include_str!("../src/main.rs");
    let materializer_start = source
        .find("fn materialize_local_audio_source(")
        .expect("desktop materializer must remain present");
    let materializer_tail = &source[materializer_start..];
    let materializer_end = materializer_tail
        .find("\n}\n\nfn parse_request_payload")
        .expect("materializer boundary must remain inspectable");
    let materializer = &materializer_tail[..materializer_end];

    assert!(
        materializer.contains("copy_bounded_local_audio_with_receipt"),
        "production materialization must retain native size+SHA-256 staging evidence"
    );
    assert!(
        materializer.contains("verify_local_audio_publication_receipt"),
        "production materialization must re-read the published app-owned source and bind it to the staging receipt"
    );
    assert!(
        !materializer.contains("copy_bounded_local_audio(source"),
        "the compatibility byte-count-only adapter must not remain on the production publication path"
    );
}

#[test]
fn local_audio_publication_must_not_overwrite_an_existing_source_name() {
    let source = include_str!("../src/main.rs");
    let materializer_start = source
        .find("fn materialize_local_audio_source(")
        .expect("desktop materializer must remain present");
    let materializer_tail = &source[materializer_start..];
    let materializer_end = materializer_tail
        .find("\n}\n\nfn parse_request_payload")
        .expect("materializer boundary must remain inspectable");
    let materializer = &materializer_tail[..materializer_end];

    assert!(
        materializer.contains("commit_local_audio_publication(&stage, &destination, project_root)"),
        "publication must use the platform-correct no-clobber durable commit boundary"
    );
    assert!(
        !materializer.contains("destination.exists()"),
        "a preflight existence check is racy and must not authorize a later overwrite-capable rename"
    );
    assert!(
        !materializer.contains("std::fs::rename(&stage, &destination)"),
        "overwrite-capable portable rename must not publish the immutable project source"
    );
}

#[test]
fn local_audio_materializer_commit_failure_must_preserve_existing_publication() {
    let source = include_str!("../src/main.rs");
    let materializer_start = source
        .find("fn materialize_local_audio_source(")
        .expect("desktop materializer must remain present");
    let materializer_tail = &source[materializer_start..];
    let materializer_end = materializer_tail
        .find("\n}\n\nfn parse_request_payload")
        .expect("materializer boundary must remain inspectable");
    let materializer = &materializer_tail[..materializer_end];
    let commit_failure_start = materializer
        .find("if commit_local_audio_publication(&stage, &destination, project_root).is_err()")
        .expect("materializer must handle publication-commit failure explicitly");
    let commit_failure_tail = &materializer[commit_failure_start..];
    let commit_failure_end = commit_failure_tail
        .find("\n    }\n\n    let published_path_metadata")
        .expect("publication failure boundary must remain inspectable");
    let commit_failure = &commit_failure_tail[..commit_failure_end];

    assert!(
        commit_failure.contains("std::fs::remove_file(&stage)"),
        "failed publication may clean up only the private stage it owns"
    );
    assert!(
        !commit_failure.contains("std::fs::remove_file(&destination)"),
        "a no-clobber collision means destination may pre-exist; the materializer must never delete it on commit failure"
    );
}

#[test]
fn local_audio_publication_commits_namespace_before_identity_authority() {
    let source = include_str!("../src/main.rs");
    let materializer_start = source
        .find("fn materialize_local_audio_source(")
        .expect("desktop materializer must remain present");
    let materializer_tail = &source[materializer_start..];
    let materializer_end = materializer_tail
        .find("\n}\n\nfn parse_request_payload")
        .expect("materializer boundary must remain inspectable");
    let materializer = &materializer_tail[..materializer_end];

    let durable_commit = materializer
        .find("commit_local_audio_publication(&stage, &destination, project_root)")
        .expect("publication must durably commit the project-owned namespace");
    let identity = materializer
        .find("build_local_audio_publication_identity(project_id, &extension, &receipt)")
        .expect("materializer must derive path-free publication identity");

    assert!(
        durable_commit < identity,
        "bootstrap/persistence identity must not be minted before the publication namespace has crossed its platform durability barrier"
    );
}

#[test]
fn local_audio_selection_retains_verified_path_free_identity_in_native_state() {
    let source = include_str!("../src/main.rs");

    assert!(
        source.contains("struct LocalAudioPublicationIdentityState"),
        "verified source identity must have a native-only state owner"
    );
    assert!(
        source.contains("build_local_audio_publication_identity(project_id, &extension, &receipt)"),
        "the production materializer must derive persistence identity from the verified native receipt"
    );
    assert!(
        source.contains("store_local_audio_publication_identity(&publication_state, publication_identity)"),
        "selection must retain native publication identity before returning bootstrap authority"
    );
    assert!(
        source.contains(".manage(LocalAudioPublicationIdentityState::default())"),
        "the native publication identity state must be registered with the Tauri runtime"
    );
}
