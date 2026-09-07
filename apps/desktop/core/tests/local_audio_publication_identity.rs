use bandscope_desktop_core::{
    build_local_audio_publication_identity, LocalAudioCopyReceipt,
};

fn receipt() -> LocalAudioCopyReceipt {
    LocalAudioCopyReceipt {
        file_size_bytes: 4,
        content_sha256:
            "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a".to_string(),
    }
}

#[test]
fn publication_identity_is_path_free_and_deterministic() {
    let identity = build_local_audio_publication_identity("project-1-1", "wav", &receipt())
        .expect("verified publication evidence should become a durable path-free identity");

    assert_eq!(identity.project_id, "project-1-1");
    assert_eq!(identity.artifact_name, "source.wav");
    assert_eq!(identity.extension, "wav");
    assert_eq!(identity.file_size_bytes, 4);
    assert_eq!(
        identity.content_sha256,
        "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
    );

    let json = serde_json::to_value(&identity).expect("publication identity should serialize");
    assert_eq!(
        json,
        serde_json::json!({
            "projectId": "project-1-1",
            "artifactName": "source.wav",
            "extension": "wav",
            "fileSizeBytes": 4,
            "contentSha256": "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
        })
    );
    assert!(json.get("sourcePath").is_none());
    assert!(json.get("path").is_none());
}

#[test]
fn publication_identity_rejects_noncanonical_or_fabricated_evidence() {
    for (project_id, extension, receipt) in [
        ("../project-1-1", "wav", receipt()),
        ("project-1-1", "WAV", receipt()),
        ("project-1-1", "exe", receipt()),
        (
            "project-1-1",
            "wav",
            LocalAudioCopyReceipt {
                file_size_bytes: 0,
                content_sha256: "00".repeat(32),
            },
        ),
        (
            "project-1-1",
            "wav",
            LocalAudioCopyReceipt {
                file_size_bytes: 4,
                content_sha256: "AA".repeat(32),
            },
        ),
        (
            "project-1-1",
            "wav",
            LocalAudioCopyReceipt {
                file_size_bytes: 4,
                content_sha256: "not-a-sha256".to_string(),
            },
        ),
    ] {
        let error = build_local_audio_publication_identity(project_id, extension, &receipt)
            .expect_err("only canonical native publication evidence may cross persistence handoff");
        assert_eq!(error, "Could not prepare the local project workspace.");
    }
}
