use bandscope_desktop_core::{read_validated_score_pdf, MAX_SCORE_PDF_BYTES};
use std::io::Write;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn unique_test_dir(name: &str) -> PathBuf {
    let suffix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after epoch")
        .as_nanos();
    std::env::temp_dir().join(format!("bandscope-{name}-{suffix}"))
}

#[test]
fn score_pdf_read_returns_only_valid_bounded_pdf_bytes() {
    let root = unique_test_dir("score-read-valid");
    std::fs::create_dir_all(&root).expect("test directory should be created");
    let path = root.join("score.pdf");
    let expected = b"%PDF-1.7\nvalidated body";
    std::fs::write(&path, expected).expect("valid PDF fixture should be written");

    let actual = read_validated_score_pdf(&path).expect("valid stored PDF should be readable");

    assert_eq!(actual, expected);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn score_pdf_read_rejects_empty_short_and_wrong_magic_content() {
    let root = unique_test_dir("score-read-invalid");
    std::fs::create_dir_all(&root).expect("test directory should be created");

    for (name, content) in [
        ("empty.pdf", b"".as_slice()),
        ("short.pdf", b"%PD".as_slice()),
        ("wrong.pdf", b"PK\x03\x04 not a PDF".as_slice()),
    ] {
        let path = root.join(name);
        std::fs::write(&path, content).expect("invalid PDF fixture should be written");
        let error = read_validated_score_pdf(&path).expect_err("invalid PDF must fail closed");
        assert!(
            error == "Could not read the score PDF." || error == "Stored score is not a valid PDF.",
            "unexpected payload-safe error: {error}"
        );
        assert!(!error.contains(root.to_string_lossy().as_ref()));
    }

    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn score_pdf_read_rejects_oversized_sparse_file_before_heap_allocation() {
    let root = unique_test_dir("score-read-oversized");
    std::fs::create_dir_all(&root).expect("test directory should be created");
    let path = root.join("oversized.pdf");
    let mut file = std::fs::File::create(&path).expect("oversized PDF fixture should be created");
    file.write_all(b"%PDF-")
        .expect("PDF magic should be written before extending sparse file");
    file.set_len(MAX_SCORE_PDF_BYTES + 1)
        .expect("sparse PDF fixture should exceed the product limit");
    drop(file);

    let error = read_validated_score_pdf(&path).expect_err("oversized PDF must fail closed");

    assert_eq!(error, "Score PDF is too large (exceeds 25MB limit).");
    let _ = std::fs::remove_dir_all(root);
}

#[cfg(unix)]
#[test]
fn score_pdf_read_rejects_non_file_descriptor() {
    let root = unique_test_dir("score-read-directory");
    std::fs::create_dir_all(&root).expect("test directory should be created");

    let error = read_validated_score_pdf(&root).expect_err("directory must fail closed");

    assert_eq!(error, "Could not read the score PDF.");
    assert!(!error.contains(root.to_string_lossy().as_ref()));
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn score_pdf_read_rejects_missing_file_without_exposing_path() {
    let root = unique_test_dir("score-read-missing");
    let path = root.join("private-score.pdf");

    let error = read_validated_score_pdf(&path).expect_err("missing PDF must fail closed");

    assert_eq!(error, "Could not read the score PDF.");
    assert!(!error.contains("private-score.pdf"));
}
