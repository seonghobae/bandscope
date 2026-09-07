# Local audio source materialization

## Problem

BandScope originally validated an OS-selected local audio file and then let later analysis reopen the canonical external filesystem path. That left analysis and restart dependent on mutable host authority: the selected file could be moved, replaced, truncated, or grown after admission. Project Persistence #962 also needs a durable source identity that does not serialize an arbitrary user filesystem path.

Resource Admission & Decode therefore owns creation and verification of the app-owned `source.<extension>` artifact and the native content identity for that publication. Project Persistence owns the later versioned project reference that consumes this evidence; it does not copy or hash user media itself.

The hardening sequence exposed distinct defects:

- source-read and app-owned destination-write failures were initially collapsed into one diagnosis;
- the one-byte over-limit probe was initially written into the disposable stage;
- the bounded copy returned only a byte count, so there was no native identity for the exact bytes written;
- SHA-256 existed in more than one security-sensitive implementation and initially had no reusable reader-only core port;
- a staging receipt alone did not prove that the final published object still contained the same bytes;
- publication verification initially read against the product-wide 100 MiB ceiling instead of the receipt's tighter expected length;
- the production Tauri materializer initially discarded the receipt and stayed on the byte-count-only adapter;
- publication initially used `destination.exists()` followed by overwrite-capable `rename`, creating a check-then-act clobber window;
- the no-clobber hard-link publication synchronized the staged file but did not explicitly cross a platform namespace-durability barrier after destination creation and private-stage removal;
- even after publication verification existed, Project Persistence still had no typed path-free handoff value for `projectId + artifactName + extension + fileSizeBytes + contentSha256`;
- after that type existed, the production selector still discarded the verified identity instead of retaining it in native state for the persistence owner.

The canonical #866 branch now repairs those defects through native retention and a platform-specific publication commit. Production local-file materialization consumes the native receipt and synchronizes the stage. On Unix it creates the immutable destination with a same-filesystem no-clobber hard link, removes the private stage name, and synchronizes the project directory. On Windows it performs a no-replace `MoveFileExW` with `MOVEFILE_WRITE_THROUGH`. Only after that commit boundary does the materializer reopen and verify the published bytes, derive `LocalAudioPublicationIdentity` from the verified receipt, and retain the path-free value in native Tauri state keyed by the locally minted project id before returning bootstrap authority. The strict analysis-runtime `LocalAudioSource` wire remains unchanged.

Project Persistence #970 has already adopted this Resource Admission ancestry in downstream Draft work: it consumes the retained identity into versioned `sourceReference`, re-admits only the app-owned artifact after restart, and snapshots the admitted bytes before analysis decode. The remaining cross-owner buyer gap is Active Player #1160's fresh audible Full mix/current-stem authority. Platform-atomic no-follow descriptor acquisition, durability of creation/replacement of higher directory ancestors, YouTube durable-source policy, and decoder licensing remain separate work.

## Constraints and invariants

- Local analysis remains local-first; this boundary adds no network authority.
- Renderer input never selects an arbitrary analysis or persistence path.
- The encoded-byte ceiling remains exactly 100 MiB.
- Metadata length before copying is not final evidence when the selected source can change during admission.
- Source-read failure and app-owned write/publication failure remain distinguishable without exposing paths or raw OS errors.
- `Interrupted` reads are retried.
- SHA-256 covers only byte slices whose staging writes succeeded. The one-byte growth probe is not admitted content and is not hashed into the receipt.
- SHA-256 is content-identity/correctness evidence only. This code does not claim CAVP validation, FIPS 140 validation, authenticity, or protection against an actor who can replace both artifact and stored digest.
- Reusable SHA-256 and publication-verification APIs accept caller-owned `Read` values and acquire no path authority.
- Publication verification consumes at most `expected.file_size_bytes + 1` bytes and rejects invalid expected lengths before reading.
- Publication must not overwrite an existing app-owned source name.
- Unix publication uses same-filesystem hard-link creation, private-stage unlink, then project-directory synchronization before the publication can mint persistence/bootstrap identity.
- Windows publication uses `MoveFileExW` without `MOVEFILE_REPLACE_EXISTING` and with `MOVEFILE_WRITE_THROUGH`; same-project staging keeps the move on one volume.
- Portable `std::fs::rename` is not the publication primitive because Rust's contract permits replacing an existing destination and platform semantics differ.
- A generic Windows directory `File::sync_all` is not treated as equivalent to Unix directory `fsync`; the Windows boundary uses the documented write-through move instead.
- The durability claim is scoped to the source publication mutation inside an already-existing app-owned project directory. Creation or replacement of higher ancestors and storage that falsely acknowledges flush completion remain outside this claim.
- The analysis-runtime `LocalAudioSource` contract remains `sourcePath + fileName + extension + fileSizeBytes`. `contentSha256` is not injected into that strict Rust/TypeScript/Python request without a versioned contract change.
- The persistence identity is a distinct contract. It contains exactly `projectId + artifactName + extension + fileSizeBytes + contentSha256`; it contains no `path` or `sourcePath` field.
- The persistence identity accepts only an existing BandScope project-id grammar, canonical lowercase admitted extension, byte size `1..=100 MiB`, and exactly 64 lowercase hexadecimal SHA-256 characters. `artifactName` is derived as `source.<extension>` rather than accepted from renderer input.
- Verified persistence identity is retained only in native Tauri state keyed by the minted project id. The renderer does not author or supply that evidence.
- If native identity state cannot be retained, local-source selection fails closed rather than returning bootstrap authority without persistence evidence.
- Portable `symlink_metadata` / open / re-check logic narrows linked-object substitution but does not claim atomic `O_NOFOLLOW` or Windows reparse-point-equivalent semantics.

## Decision record

1. Keep the external canonical path and revalidate before every analysis — rejected. Restart and persistence would still depend on mutable host authority.
2. Persist the absolute external path — rejected. It widens disclosure and violates #962's path-free direction.
3. Copy the selected file into app-owned `source.<extension>` — selected. Later analysis can use BandScope-owned authority.
4. Keep `std::io::copy` and one generic error — rejected. Explicit bounded read/write preserves the ceiling while distinguishing source and destination failures.
5. Hash later in the renderer or from the original path — rejected. Neither is authoritative for bytes actually staged into BandScope storage.
6. Add another SHA-256 implementation in persistence or Active Player — rejected. `bandscope_desktop_core::sha256_hex_reader` is the reader-only Shared Kernel.
7. Treat the staging receipt as publication truth without rereading — rejected. Same-size mutation would evade byte-count checks.
8. Re-read every published object up to 100 MiB — rejected. The native receipt gives a tighter expected length.
9. Leave the Tauri caller on `copy_bounded_local_audio -> u64` — rejected. Production publication must retain native size+digest evidence and verify the publication before bootstrap authority is returned.
10. Check `destination.exists()` and then rename the stage — rejected. On overwrite-capable rename semantics the sequence is racy.
11. Use portable `std::fs::rename` without a preflight check — rejected. Rust permits replacement of an existing destination and does not provide one cross-platform durability contract for the namespace mutation.
12. Use one generic directory-sync implementation on Unix and Windows — rejected. Unix directory synchronization and Windows directory-handle/flush contracts are not interchangeable enough to justify a portable-looking claim.
13. On Unix, create the destination with `std::fs::hard_link(stage, destination)`, remove the private stage name, then synchronize the project directory — selected. Destination creation is no-clobber and the final namespace state crosses an explicit directory durability barrier.
14. On Windows, use `MoveFileExW(stage, destination, MOVEFILE_WRITE_THROUGH)` without `MOVEFILE_REPLACE_EXISTING` — selected. Existing destinations fail closed and the documented write-through flag keeps the move from returning before the move reaches disk.
15. Add `contentSha256` to the existing analysis `LocalAudioSource` payload — rejected. Python admission is strict and this would mix persistence evidence with a narrower runtime request.
16. Define a separate path-free `LocalAudioPublicationIdentity` whose artifact name is derived from canonical native evidence — selected. This keeps Resource Admission as the copy/hash authority and gives #970 a serializable persistence input without absolute paths.
17. Return bootstrap authority while leaving the verified identity only in a local stack variable — rejected. The selector retains the typed identity in native Tauri state keyed by project id before returning; downstream persistence can adopt that native evidence without trusting renderer-authored digest/path data.

## Implementation and exact evidence

The cumulative hardening remains test-first where behavior changed:

- `dbeee9c7407c72f999f584eb0eb9342ddc39fddd` adopted protected `develop@314ddeae7b775a4957594b599358c8255617eb2e` through ordinary non-force ancestry.
- RED `804a2867e877947feaffb1da6c6072e6a49049fe` and fix `0beee45b98e51ba46b571a82c6d0d93db61ea8d6` established exact-limit acceptance and one-byte-over rejection.
- `a2b1bd9e33a69be75f813f005abd37345200ce55` moved successful local-file intake to an app-owned same-project stage; `323a7fac00c4954af12b382802a9d6f8359ef4c5` exported the core port to Tauri.
- Diagnostics RED `131d6d7220985abd207559e6eb5dc122ac989cf4` and fix `ac4adfdb5df82f48aadd5e028433e3336d3ce2ae` separated source-read and destination-write failures and made the one-byte over-limit check read-only.
- Content-identity RED `dc413794fb84c736085ab77b763854ba0f58bdf1` and fix `566cd1f991296e7f3c288cb07a11c2d2effb258a` introduced `LocalAudioCopyReceipt { file_size_bytes, content_sha256 }`.
- Shared-kernel RED `373824c7bbb40f2df1bb2721316680378c104834` and fix `d1ba40683772019577fec4d8c767ff8b23294e38` exposed reader-only `sha256_hex_reader`.
- Publication RED `fdfdd7003b8a9162f846dcf22ffe66a3afd5f47e` and fix `a1c85cbfbdc7051169f097e8ad235e3bbac439d3` introduced `verify_local_audio_publication_receipt`; `20e7faaddd619c6cbd053876ca6de27b9933a4a2` exported it.
- Bounded-verification RED `6a0692ee288d3b126bd0598e07e03c88a702d567` and fix `c65a9fd312f4d67e6d1cad83b80b1213e692c8dd` changed publication verification to stop after expected bytes plus one growth probe.
- Production-integration RED `ed9fe7eba6261753dc0f68e820e2b642703fe2cd` and fix `bdf8f87d5e5c9db423537c7633e7ff4b92bec5b6` moved the Tauri materializer onto native receipt + publication verification.
- No-clobber RED `45b1f72abeded4e478775d31085244621f68c9f0` and fix `eb972e951ef090c92b595c752b18d66f11f6b96e` replaced check-then-rename with same-filesystem hard-link publication.
- Path-free handoff RED `bad908c83bfb89f545f0f2f637d96ac8fdfa3e0e` requires exact camelCase serialization of the five persistence fields, no path fields, and fail-closed rejection of invalid native evidence.
- Path-free handoff fix `87bdeea92d3bb6dc45eb666f422bd8a3d36f3872` adds `LocalAudioPublicationIdentity` and `build_local_audio_publication_identity`; export `344a9a39f32ac40b3e137c76e2cfd46243827bb5` makes the contract available from `bandscope_desktop_core` to the persistence owner.
- An earlier exploratory retention RED `cbfa967b16e94f2d84940665ce38537075a8ce41` was intentionally neutralized by `d8c57ce1d64d0bc9963219740aeaa83d9569a90b` rather than leaving a known failing head; those two commits add no production claim.
- Production native-retention RED `106ae75cad85553e56964a9844ea7a01f6ce456c` requires the materializer to derive the typed identity from the verified receipt, the selector to store it in native state, and Tauri to register that state.
- Native-retention fix `e4e2ba734bc80304a754ce2eb52e473fd9ee3631` returns `LocalAudioSourcePayload + LocalAudioPublicationIdentity` from materialization, stores the identity in `LocalAudioPublicationIdentityState` before bootstrap authority is returned, and registers the native state with the Tauri runtime.
- Publication-durability RED `ebc505504afd06bab55dfc4ba64aa312f7aa848e` requires a dedicated commit boundary and requires that boundary to occur before path-free identity is minted. It was followed immediately by implementation descendants, so no hosted RED failure is claimed.
- `d7945553c334fb192bf316ad21ddc790983952c9` introduced the platform-specific publication module; `4e2bccc5986070ac60937ff9ac481696ea898671` repaired its unit-test import before production integration.
- Production fix `94086edb9749cf82708718abc31a46fbbaaf7742` moves the Tauri materializer onto that commit boundary: Unix hard-link + stage unlink + project-directory sync; Windows no-replace `MoveFileExW` + `MOVEFILE_WRITE_THROUGH`. Publication verification and native identity follow the durability barrier rather than preceding it.

The SHA-256 implementation is checked against standard known-answer vectors including the empty message, `abc`, the multi-block vector, and one million `a` bytes. Those are correctness regressions, not validation-module evidence.

## Security Notes

The selected audio path, file metadata, and media bytes are untrusted. The OS file dialog supplies initial user authority; BandScope uses that path only to canonicalize and open the source. The project-owned artifact is the authority after successful admission.

The production Tauri materializer synchronizes the staged file before publication. Unix then creates the destination through a no-clobber same-filesystem hard link, removes the private stage name, and synchronizes the project directory. Windows uses a same-project `MoveFileExW` with `MOVEFILE_WRITE_THROUGH` and without a replace-existing flag. Failure at the publication boundary returns the existing path-redacted project-workspace diagnosis; no path-free source identity is minted on that failed call.

After the platform commit, the materializer requires regular/non-symlink path observations, opens the publication, checks descriptor size, verifies exact receipt equality, and performs a post-verification path check. Publication mismatch or read failure is normalized to the bounded project-workspace diagnosis; source/destination paths, raw OS errors, and audio bytes are not exposed.

`LocalAudioPublicationIdentity` does not acquire filesystem authority. It converts already verified native evidence into a deterministic, path-free value for the persistence boundary. Invalid project ids, extensions, byte counts, or digest encodings fail closed. Production local-file selection retains that value in native Tauri state before returning the ordinary bootstrap summary, so the renderer does not need to invent a digest or persist a host path.

Residual risk remains explicit. The boundary does not claim platform-atomic no-follow acquisition; it does not claim that creation/replacement of higher project-root ancestors has been durably committed by this source-publication operation; and it cannot compensate for storage that reports successful flush/write-through before durable media persistence.

No new logging, telemetry, network transfer, or raw-media export is introduced. The SHA-256 receipt and publication identity are non-secret content identity.

## Test and acceptance points

- exact 100 MiB encoded-byte limit accepted; one byte over rejected;
- empty source rejected;
- source-reader and destination-writer failures remain distinct and path-safe;
- `Interrupted` reads retry without changing identity;
- failed writes cannot return a partial receipt;
- the growth probe is neither staged nor hashed;
- unchanged published bytes reproduce the staging receipt;
- same-size mutation, truncation, growth, or publication-read failure fails closed;
- grown publication stops after expected bytes plus one probe;
- production publication cannot use existence-check plus overwrite-capable rename;
- an existing destination remains unchanged and publication fails closed;
- publication paths must remain direct children of the app-owned project root;
- Unix publication synchronizes the project directory after the final destination/stage namespace mutations;
- Windows publication requests no replacement and uses `MOVEFILE_WRITE_THROUGH`;
- production Tauri local-file materialization consumes receipt, durable publication-commit, and publication-verification boundaries, not the compatibility byte-count adapter;
- path-free identity is minted only after the platform publication commit;
- path-free identity serializes exactly the five persistence fields and cannot serialize `path`/`sourcePath`;
- invalid project ids, uppercase/unsupported extensions, zero/oversized byte counts, and noncanonical SHA-256 encodings are rejected;
- production local-file selection derives identity from the verified receipt and retains it in registered native Tauri state before returning bootstrap authority;
- hosted Rust/Tauri, Windows, macOS, security, SBOM, coverage/package, and independent-review evidence must be reacquired on the final exact #866 head.

Synthetic test bytes exercise the filesystem unit boundary only. They do not substitute for production scientific acceptance. Rights-cleared real decoded audio still has to exercise the integrated Windows/macOS intake/decode/analysis/playback path where the relevant commercial claim is made.

## Remaining risks and follow-up

The local-file path now has separate native contracts for bytes, namespace publication and durable identity: `LocalAudioCopyReceipt` proves exact staged/published content; `commit_local_audio_publication` closes the supported-platform source-name publication barrier; retained `LocalAudioPublicationIdentity` represents path-free durable evidence for Project Persistence.

Project Persistence #970 already consumes that evidence in downstream Draft code, re-admits the app-owned source on restart and snapshots admitted bytes for analysis. Active Player #1160 must still combine persisted `selectedPlaybackSource` intent with fresh native Full mix/current-stem availability; missing preferred stems fail closed to Full mix. When #866/#970 ancestry enters that stack, the private playable-stem SHA-256 implementation should be deleted in favor of `bandscope_desktop_core::sha256_hex_reader` while preserving stem identity/error tests.

YouTube intake still uses its owned cache artifact and needs an explicit durable-source promotion decision. Platform-atomic no-follow acquisition and higher-ancestor crash durability remain Resource Admission/platform work. Issue #1129 remains the commercial decoder-dependency gate.

## References

Microsoft. (2023). *MoveFileExW function (winbase.h).* Microsoft Learn. https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-movefileexw

Microsoft. (2025). *Directory handles.* Microsoft Learn. https://learn.microsoft.com/windows/win32/fileio/directory-handles

National Institute of Standards and Technology. (2015). *Secure Hash Standard (SHS)* (FIPS PUB 180-4). https://doi.org/10.6028/NIST.FIPS.180-4

National Institute of Standards and Technology. (2023, March 7). *Decision to revise FIPS 180-4, Secure Hash Standard (SHS).* https://csrc.nist.gov/news/2023/decision-to-revise-fips-180-4

Rust Project Developers. (2026). *std::fs::rename.* Rust standard library documentation. https://doc.rust-lang.org/std/fs/fn.rename.html
