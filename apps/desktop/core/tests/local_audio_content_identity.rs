use bandscope_desktop_core::{
    copy_bounded_local_audio_with_receipt, verify_local_audio_publication_receipt,
};
use std::io::Cursor;

#[test]
fn local_audio_copy_receipt_hashes_exact_admitted_bytes() {
    let input = vec![1_u8, 2, 3, 4];
    let mut staged = Vec::new();

    let receipt = copy_bounded_local_audio_with_receipt(Cursor::new(&input), &mut staged)
        .expect("bounded admission should return content identity for the bytes it stages");

    assert_eq!(staged, input);
    assert_eq!(receipt.file_size_bytes, 4);
    assert_eq!(
        receipt.content_sha256,
        "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
    );
}

#[test]
fn publication_receipt_requires_the_published_bytes_to_match_the_stage() {
    let input = vec![1_u8, 2, 3, 4];
    let mut staged = Vec::new();
    let staged_receipt = copy_bounded_local_audio_with_receipt(Cursor::new(&input), &mut staged)
        .expect("staging should produce native identity evidence");

    let published_receipt = verify_local_audio_publication_receipt(
        Cursor::new(&staged),
        &staged_receipt,
    )
    .expect("unchanged published bytes should retain the staging identity");

    assert_eq!(published_receipt, staged_receipt);

    let mismatch = verify_local_audio_publication_receipt(
        Cursor::new(vec![1_u8, 2, 3, 5]),
        &staged_receipt,
    )
    .expect_err("same-size mutation after staging must fail publication binding");
    assert_eq!(mismatch, "Could not prepare the local project workspace.");
}
