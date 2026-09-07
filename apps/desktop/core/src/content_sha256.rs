//! Streaming SHA-256 for local content-identity receipts.
//!
//! The operations and constants follow NIST FIPS 180-4 SHA-256. The known-answer
//! tests below are correctness checks, not CAVP validation or a FIPS 140 claim.

use std::io::{self, ErrorKind, Read};

const BLOCK_BYTES: usize = 64;
const DIGEST_BYTES: usize = 32;
const INITIAL_STATE: [u32; 8] = [
    0x6a09_e667,
    0xbb67_ae85,
    0x3c6e_f372,
    0xa54f_f53a,
    0x510e_527f,
    0x9b05_688c,
    0x1f83_d9ab,
    0x5be0_cd19,
];
const ROUND_CONSTANTS: [u32; 64] = [
    0x428a_2f98, 0x7137_4491, 0xb5c0_fbcf, 0xe9b5_dba5, 0x3956_c25b, 0x59f1_11f1,
    0x923f_82a4, 0xab1c_5ed5, 0xd807_aa98, 0x1283_5b01, 0x2431_85be, 0x550c_7dc3,
    0x72be_5d74, 0x80de_b1fe, 0x9bdc_06a7, 0xc19b_f174, 0xe49b_69c1, 0xefbe_4786,
    0x0fc1_9dc6, 0x240c_a1cc, 0x2de9_2c6f, 0x4a74_84aa, 0x5cb0_a9dc, 0x76f9_88da,
    0x983e_5152, 0xa831_c66d, 0xb003_27c8, 0xbf59_7fc7, 0xc6e0_0bf3, 0xd5a7_9147,
    0x06ca_6351, 0x1429_2967, 0x27b7_0a85, 0x2e1b_2138, 0x4d2c_6dfc, 0x5338_0d13,
    0x650a_7354, 0x766a_0abb, 0x81c2_c92e, 0x9272_2c85, 0xa2bf_e8a1, 0xa81a_664b,
    0xc24b_8b70, 0xc76c_51a3, 0xd192_e819, 0xd699_0624, 0xf40e_3585, 0x106a_a070,
    0x19a4_c116, 0x1e37_6c08, 0x2748_774c, 0x34b0_bcb5, 0x391c_0cb3, 0x4ed8_aa4a,
    0x5b9c_ca4f, 0x682e_6ff3, 0x748f_82ee, 0x78a5_636f, 0x84c8_7814, 0x8cc7_0208,
    0x90be_fffa, 0xa450_6ceb, 0xbef9_a3f7, 0xc671_78f2,
];

#[derive(Clone)]
pub(crate) struct StreamingSha256 {
    words: [u32; 8],
    buffer: [u8; BLOCK_BYTES],
    buffer_len: usize,
    message_len_bytes: u64,
}

impl Default for StreamingSha256 {
    fn default() -> Self {
        Self {
            words: INITIAL_STATE,
            buffer: [0; BLOCK_BYTES],
            buffer_len: 0,
            message_len_bytes: 0,
        }
    }
}

impl StreamingSha256 {
    /// Add the next contiguous admitted byte slice to this digest state.
    pub(crate) fn update(&mut self, mut bytes: &[u8]) -> Result<(), ()> {
        self.message_len_bytes = self
            .message_len_bytes
            .checked_add(bytes.len() as u64)
            .ok_or(())?;

        if self.buffer_len != 0 {
            let copied = (BLOCK_BYTES - self.buffer_len).min(bytes.len());
            self.buffer[self.buffer_len..self.buffer_len + copied]
                .copy_from_slice(&bytes[..copied]);
            self.buffer_len += copied;
            bytes = &bytes[copied..];
            if self.buffer_len == BLOCK_BYTES {
                let block = self.buffer;
                self.compress(&block);
                self.buffer_len = 0;
            }
        }

        while bytes.len() >= BLOCK_BYTES {
            let block: &[u8; BLOCK_BYTES] = bytes[..BLOCK_BYTES].try_into().map_err(|_| ())?;
            self.compress(block);
            bytes = &bytes[BLOCK_BYTES..];
        }

        if !bytes.is_empty() {
            self.buffer[..bytes.len()].copy_from_slice(bytes);
            self.buffer_len = bytes.len();
        }
        Ok(())
    }

    /// Finalize the digest as canonical lowercase hexadecimal.
    pub(crate) fn finalize_hex(mut self) -> Result<String, ()> {
        let message_len_bits = self.message_len_bytes.checked_mul(8).ok_or(())?;

        self.buffer[self.buffer_len] = 0x80;
        self.buffer_len += 1;
        if self.buffer_len > 56 {
            self.buffer[self.buffer_len..].fill(0);
            let block = self.buffer;
            self.compress(&block);
            self.buffer = [0; BLOCK_BYTES];
            self.buffer_len = 0;
        }
        self.buffer[self.buffer_len..56].fill(0);
        self.buffer[56..].copy_from_slice(&message_len_bits.to_be_bytes());
        let block = self.buffer;
        self.compress(&block);

        let mut digest = [0_u8; DIGEST_BYTES];
        for (index, word) in self.words.into_iter().enumerate() {
            digest[index * 4..index * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }

        let mut encoded = String::with_capacity(DIGEST_BYTES * 2);
        const HEX: &[u8; 16] = b"0123456789abcdef";
        for byte in digest {
            encoded.push(HEX[(byte >> 4) as usize] as char);
            encoded.push(HEX[(byte & 0x0f) as usize] as char);
        }
        Ok(encoded)
    }

    fn compress(&mut self, block: &[u8; BLOCK_BYTES]) {
        let mut schedule = [0_u32; 64];
        for (index, chunk) in block.chunks_exact(4).enumerate() {
            schedule[index] = u32::from_be_bytes(
                chunk
                    .try_into()
                    .expect("SHA-256 message word always contains four bytes"),
            );
        }
        for index in 16..64 {
            let small_sigma0 = schedule[index - 15].rotate_right(7)
                ^ schedule[index - 15].rotate_right(18)
                ^ (schedule[index - 15] >> 3);
            let small_sigma1 = schedule[index - 2].rotate_right(17)
                ^ schedule[index - 2].rotate_right(19)
                ^ (schedule[index - 2] >> 10);
            schedule[index] = schedule[index - 16]
                .wrapping_add(small_sigma0)
                .wrapping_add(schedule[index - 7])
                .wrapping_add(small_sigma1);
        }

        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = self.words;
        for index in 0..64 {
            let big_sigma1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let choose = (e & f) ^ ((!e) & g);
            let temporary1 = h
                .wrapping_add(big_sigma1)
                .wrapping_add(choose)
                .wrapping_add(ROUND_CONSTANTS[index])
                .wrapping_add(schedule[index]);
            let big_sigma0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let majority = (a & b) ^ (a & c) ^ (b & c);
            let temporary2 = big_sigma0.wrapping_add(majority);

            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temporary1);
            d = c;
            c = b;
            b = a;
            a = temporary1.wrapping_add(temporary2);
        }

        self.words[0] = self.words[0].wrapping_add(a);
        self.words[1] = self.words[1].wrapping_add(b);
        self.words[2] = self.words[2].wrapping_add(c);
        self.words[3] = self.words[3].wrapping_add(d);
        self.words[4] = self.words[4].wrapping_add(e);
        self.words[5] = self.words[5].wrapping_add(f);
        self.words[6] = self.words[6].wrapping_add(g);
        self.words[7] = self.words[7].wrapping_add(h);
    }
}

/// Hash a caller-owned byte stream as canonical lowercase SHA-256.
///
/// Security Notes: this helper never opens a path, logs bytes, or grants filesystem
/// authority. The caller must supply an already-authorized reader and decide how
/// the resulting digest is bound to a concrete artifact. `Interrupted` reads are
/// retried; other reader failures are returned unchanged. This is content identity,
/// not an authenticity primitive or a FIPS module-validation claim.
pub fn sha256_hex_reader(mut reader: impl Read) -> io::Result<String> {
    let mut digest = StreamingSha256::default();
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        match reader.read(&mut chunk) {
            Ok(0) => break,
            Ok(read_bytes) => digest
                .update(&chunk[..read_bytes])
                .map_err(|_| io::Error::new(ErrorKind::InvalidData, "SHA-256 input too large"))?,
            Err(error) if error.kind() == ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    digest
        .finalize_hex()
        .map_err(|_| io::Error::new(ErrorKind::InvalidData, "SHA-256 input too large"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Error};

    fn digest_in_chunks(bytes: &[u8], chunk_size: usize) -> String {
        let mut digest = StreamingSha256::default();
        for chunk in bytes.chunks(chunk_size) {
            digest.update(chunk).expect("test vector length must fit SHA-256");
        }
        digest
            .finalize_hex()
            .expect("test vector bit length must fit SHA-256")
    }

    struct InterruptedShortReader {
        bytes: Vec<u8>,
        cursor: usize,
        interrupted: bool,
    }

    impl Read for InterruptedShortReader {
        fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
            if !self.interrupted {
                self.interrupted = true;
                return Err(Error::from(ErrorKind::Interrupted));
            }
            if self.cursor == self.bytes.len() {
                return Ok(0);
            }
            let copied = 7.min(output.len()).min(self.bytes.len() - self.cursor);
            output[..copied].copy_from_slice(&self.bytes[self.cursor..self.cursor + copied]);
            self.cursor += copied;
            Ok(copied)
        }
    }

    struct FailingReader;

    impl Read for FailingReader {
        fn read(&mut self, _output: &mut [u8]) -> io::Result<usize> {
            Err(Error::new(ErrorKind::Other, "fixture read failure"))
        }
    }

    #[test]
    fn matches_sha256_known_answer_vectors() {
        for (message, expected) in [
            (
                &b""[..],
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ),
            (
                &b"abc"[..],
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            ),
            (
                &b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"[..],
                "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
            ),
        ] {
            assert_eq!(digest_in_chunks(message, 7), expected);
        }
    }

    #[test]
    fn shared_reader_retries_interrupted_short_reads() {
        let bytes = (0..131_111)
            .map(|index| (index % 251) as u8)
            .collect::<Vec<_>>();
        let expected = sha256_hex_reader(Cursor::new(&bytes)).expect("reference hash should succeed");
        let actual = sha256_hex_reader(InterruptedShortReader {
            bytes,
            cursor: 0,
            interrupted: false,
        })
        .expect("interrupted short reads should be retried");
        assert_eq!(actual, expected);
    }

    #[test]
    fn shared_reader_propagates_non_interrupted_failure() {
        let error = sha256_hex_reader(FailingReader).expect_err("reader failure must propagate");
        assert_eq!(error.kind(), ErrorKind::Other);
    }

    #[test]
    fn matches_the_million_a_vector() {
        assert_eq!(
            digest_in_chunks(&vec![b'a'; 1_000_000], 64 * 1024),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }
}
