use crate::content_sha256::StreamingSha256;
use std::io::{ErrorKind, Read, Write};

/// Maximum encoded local-audio file size accepted by the desktop bootstrap boundary.
pub const MAX_LOCAL_AUDIO_FILE_BYTES: u64 = 100 * 1024 * 1024;

const LOCAL_AUDIO_READ_ERROR: &str = "Could not read the selected audio file.";
const LOCAL_AUDIO_WRITE_ERROR: &str = "Could not prepare the local project workspace.";
const LOCAL_AUDIO_TOO_LARGE_ERROR: &str =
    "Choose a shorter or smaller song file to start analysis.";

/// Immutable identity evidence for one successfully staged local-audio byte stream.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LocalAudioCopyReceipt {
    /// Exact number of bytes written successfully to the staging writer.
    pub file_size_bytes: u64,
    /// SHA-256 of exactly the bytes written successfully, encoded as lowercase hexadecimal.
    pub content_sha256: String,
}

/// Validate a native local-audio file length before storing bootstrap metadata.
///
/// The caller must obtain this length from the native filesystem descriptor or
/// metadata boundary rather than from renderer-controlled JSON. The function
/// intentionally returns only bounded product messages and never includes a
/// local path or payload content.
pub fn validate_local_audio_file_size(file_size_bytes: u64) -> Result<u64, String> {
    if file_size_bytes == 0 {
        return Err(LOCAL_AUDIO_READ_ERROR.to_string());
    }
    if file_size_bytes > MAX_LOCAL_AUDIO_FILE_BYTES {
        return Err(LOCAL_AUDIO_TOO_LARGE_ERROR.to_string());
    }
    Ok(file_size_bytes)
}

fn read_retrying_interrupted(reader: &mut impl Read, buffer: &mut [u8]) -> Result<usize, String> {
    loop {
        match reader.read(buffer) {
            Ok(read) => return Ok(read),
            Err(error) if error.kind() == ErrorKind::Interrupted => continue,
            Err(_) => return Err(LOCAL_AUDIO_READ_ERROR.to_string()),
        }
    }
}

fn copy_bounded_local_audio_with_limit<R: Read, W: Write>(
    mut reader: R,
    writer: &mut W,
    max_bytes: u64,
) -> Result<LocalAudioCopyReceipt, String> {
    let mut copied = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    let mut content_digest = StreamingSha256::default();

    loop {
        if copied == max_bytes {
            let mut overflow_probe = [0_u8; 1];
            let read = read_retrying_interrupted(&mut reader, &mut overflow_probe)?;
            if read == 0 {
                break;
            }
            return Err(LOCAL_AUDIO_TOO_LARGE_ERROR.to_string());
        }

        let remaining = (max_bytes - copied).min(buffer.len() as u64) as usize;
        let read = read_retrying_interrupted(&mut reader, &mut buffer[..remaining])?;
        if read == 0 {
            break;
        }
        writer
            .write_all(&buffer[..read])
            .map_err(|_| LOCAL_AUDIO_WRITE_ERROR.to_string())?;
        content_digest
            .update(&buffer[..read])
            .map_err(|_| LOCAL_AUDIO_READ_ERROR.to_string())?;
        copied += read as u64;
    }

    if copied == 0 {
        return Err(LOCAL_AUDIO_READ_ERROR.to_string());
    }
    let content_sha256 = content_digest
        .finalize_hex()
        .map_err(|_| LOCAL_AUDIO_READ_ERROR.to_string())?;
    Ok(LocalAudioCopyReceipt {
        file_size_bytes: copied,
        content_sha256,
    })
}

/// Copy one admitted local-audio stream into a staging writer and return native content identity.
///
/// Security Notes: callers must pass an already-open, OS-authorized source
/// descriptor and a private app-owned staging writer. The helper writes no more
/// than the 100 MiB ceiling, hashes exactly the bytes whose writes succeeded,
/// and, after reaching the ceiling exactly, reads only one probe byte to detect
/// source growth. Source-read and destination-write failures use distinct
/// bounded product errors so storage failures are not misdiagnosed as bad media.
/// The caller must discard the staging artifact on error, synchronize it before
/// publication, and bind the returned receipt only to the artifact that was
/// actually published.
pub fn copy_bounded_local_audio_with_receipt<R: Read, W: Write>(
    reader: R,
    writer: &mut W,
) -> Result<LocalAudioCopyReceipt, String> {
    copy_bounded_local_audio_with_limit(reader, writer, MAX_LOCAL_AUDIO_FILE_BYTES)
}

/// Re-read a published app-owned source and prove that it matches its staging receipt.
///
/// Security Notes: the caller must pass an already-open descriptor for the
/// synchronized, published `source.<extension>` object. This helper opens no
/// path and grants no filesystem authority. The staging receipt is native
/// evidence from the prior bounded copy, so its byte length becomes the tighter
/// publication-read ceiling: the verifier hashes at most that many bytes and
/// reads one additional probe byte to reject growth. It then requires both size
/// and digest to equal the staging receipt. Any invalid expected length, read,
/// growth, truncation, or content mismatch is reported as a bounded
/// project-workspace failure because the selected source already passed
/// admission before publication.
pub fn verify_local_audio_publication_receipt<R: Read>(
    reader: R,
    expected: &LocalAudioCopyReceipt,
) -> Result<LocalAudioCopyReceipt, String> {
    if expected.file_size_bytes == 0 || expected.file_size_bytes > MAX_LOCAL_AUDIO_FILE_BYTES {
        return Err(LOCAL_AUDIO_WRITE_ERROR.to_string());
    }

    let mut sink = std::io::sink();
    let actual = copy_bounded_local_audio_with_limit(reader, &mut sink, expected.file_size_bytes)
        .map_err(|_| LOCAL_AUDIO_WRITE_ERROR.to_string())?;
    if actual != *expected {
        return Err(LOCAL_AUDIO_WRITE_ERROR.to_string());
    }
    Ok(actual)
}

/// Copy one admitted local-audio stream into a staging writer and return its byte count.
///
/// This compatibility adapter preserves the existing desktop call boundary while
/// callers migrate to `copy_bounded_local_audio_with_receipt`. It uses the same
/// bounded copy and content-hash path and discards only the returned digest.
pub fn copy_bounded_local_audio<R: Read, W: Write>(reader: R, writer: &mut W) -> Result<u64, String> {
    copy_bounded_local_audio_with_receipt(reader, writer).map(|receipt| receipt.file_size_bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Error};

    struct FailingWriter;

    impl Write for FailingWriter {
        fn write(&mut self, _buffer: &[u8]) -> std::io::Result<usize> {
            Err(Error::new(ErrorKind::Other, "simulated destination failure"))
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    struct FailingReader;

    impl Read for FailingReader {
        fn read(&mut self, _buffer: &mut [u8]) -> std::io::Result<usize> {
            Err(Error::new(ErrorKind::Other, "simulated source failure"))
        }
    }

    struct InterruptedThenReader {
        bytes: Cursor<Vec<u8>>,
        interrupted: bool,
    }

    impl Read for InterruptedThenReader {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            if !self.interrupted {
                self.interrupted = true;
                return Err(Error::from(ErrorKind::Interrupted));
            }
            self.bytes.read(buffer)
        }
    }

    struct CountingReader {
        bytes: Cursor<Vec<u8>>,
        bytes_read: usize,
    }

    impl Read for CountingReader {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            let read = self.bytes.read(buffer)?;
            self.bytes_read += read;
            Ok(read)
        }
    }

    #[test]
    fn bounded_copy_rejects_stream_growth_without_staging_bytes_past_the_limit() {
        let input = Cursor::new(vec![1_u8, 2, 3, 4, 5]);
        let mut staged = Vec::new();

        let error = copy_bounded_local_audio_with_limit(input, &mut staged, 4)
            .expect_err("a source that grows beyond the admitted byte limit must fail closed");

        assert_eq!(error, LOCAL_AUDIO_TOO_LARGE_ERROR);
        assert_eq!(staged, vec![1_u8, 2, 3, 4]);
    }

    #[test]
    fn bounded_copy_accepts_the_exact_limit_and_reports_content_identity() {
        let input = Cursor::new(vec![1_u8, 2, 3, 4]);
        let mut staged = Vec::new();

        let receipt = copy_bounded_local_audio_with_limit(input, &mut staged, 4)
            .expect("the exact encoded-byte limit remains admissible");

        assert_eq!(receipt.file_size_bytes, 4);
        assert_eq!(
            receipt.content_sha256,
            "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
        );
        assert_eq!(staged, vec![1_u8, 2, 3, 4]);
    }

    #[test]
    fn bounded_copy_reports_destination_failure_as_workspace_failure() {
        let input = Cursor::new(vec![1_u8, 2, 3, 4]);
        let mut staged = FailingWriter;

        let error = copy_bounded_local_audio_with_limit(input, &mut staged, 4)
            .expect_err("a staging write failure must not be reported as a source read failure");

        assert_eq!(error, LOCAL_AUDIO_WRITE_ERROR);
    }

    #[test]
    fn bounded_copy_keeps_source_failure_distinct_from_workspace_failure() {
        let input = FailingReader;
        let mut staged = Vec::new();

        let error = copy_bounded_local_audio_with_limit(input, &mut staged, 4)
            .expect_err("a source read failure must retain the media-read diagnosis");

        assert_eq!(error, LOCAL_AUDIO_READ_ERROR);
        assert!(staged.is_empty());
    }

    #[test]
    fn bounded_copy_retries_interrupted_source_reads_without_changing_identity() {
        let input = InterruptedThenReader {
            bytes: Cursor::new(vec![1_u8, 2, 3, 4]),
            interrupted: false,
        };
        let mut staged = Vec::new();

        let receipt = copy_bounded_local_audio_with_limit(input, &mut staged, 4)
            .expect("an interrupted source read should be retried");

        assert_eq!(receipt.file_size_bytes, 4);
        assert_eq!(
            receipt.content_sha256,
            "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
        );
        assert_eq!(staged, vec![1_u8, 2, 3, 4]);
    }

    #[test]
    fn publication_verification_maps_read_failure_to_workspace_failure() {
        let expected = LocalAudioCopyReceipt {
            file_size_bytes: 4,
            content_sha256:
                "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
                    .to_string(),
        };

        let error = verify_local_audio_publication_receipt(FailingReader, &expected)
            .expect_err("published artifact read failure must be a workspace failure");

        assert_eq!(error, LOCAL_AUDIO_WRITE_ERROR);
    }

    #[test]
    fn publication_verification_stops_after_expected_size_plus_one_probe_byte() {
        let expected = LocalAudioCopyReceipt {
            file_size_bytes: 4,
            content_sha256:
                "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a"
                    .to_string(),
        };
        let mut published = CountingReader {
            bytes: Cursor::new(vec![1_u8, 2, 3, 4, 5, 6, 7, 8]),
            bytes_read: 0,
        };

        let error = verify_local_audio_publication_receipt(&mut published, &expected)
            .expect_err("a grown published artifact must fail without scanning unrelated tail bytes");

        assert_eq!(error, LOCAL_AUDIO_WRITE_ERROR);
        assert_eq!(published.bytes_read, 5);
    }

    #[test]
    fn publication_verification_rejects_impossible_expected_lengths_without_reading() {
        for file_size_bytes in [0, MAX_LOCAL_AUDIO_FILE_BYTES + 1] {
            let expected = LocalAudioCopyReceipt {
                file_size_bytes,
                content_sha256: "00".repeat(32),
            };
            let mut published = CountingReader {
                bytes: Cursor::new(vec![1_u8, 2, 3, 4]),
                bytes_read: 0,
            };

            let error = verify_local_audio_publication_receipt(&mut published, &expected)
                .expect_err("an impossible native receipt length must fail before reading");

            assert_eq!(error, LOCAL_AUDIO_WRITE_ERROR);
            assert_eq!(published.bytes_read, 0);
        }
    }
}
