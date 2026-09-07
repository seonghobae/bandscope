use std::{io, path::Path};

/// Commit a synchronized local-audio stage into its immutable project-owned name.
///
/// The caller must have already synchronized `stage`. Both paths must be direct
/// children of the same app-owned project root. The operation is no-clobber on
/// every supported platform: Unix publishes with a hard link, removes the
/// private stage name, then synchronizes the project directory; Windows uses
/// `MoveFileExW` without `MOVEFILE_REPLACE_EXISTING` and with
/// `MOVEFILE_WRITE_THROUGH` so the namespace move crosses the platform's
/// write-through boundary before authority is returned.
///
/// This barrier covers the publication mutation inside an already-existing
/// project directory. It does not claim durability for creation or replacement
/// of higher ancestors, nor can it override storage that falsely acknowledges a
/// completed flush.
pub(crate) fn commit_local_audio_publication(
    stage: &Path,
    destination: &Path,
    project_root: &Path,
) -> io::Result<()> {
    if stage.parent() != Some(project_root) || destination.parent() != Some(project_root) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "local-audio publication paths must be direct project children",
        ));
    }

    commit_local_audio_publication_platform(stage, destination, project_root)
}

#[cfg(unix)]
fn commit_local_audio_publication_platform(
    stage: &Path,
    destination: &Path,
    project_root: &Path,
) -> io::Result<()> {
    std::fs::hard_link(stage, destination)?;
    if let Err(error) = std::fs::remove_file(stage) {
        let _ = std::fs::remove_file(destination);
        return Err(error);
    }

    if let Err(error) = sync_project_directory(project_root) {
        let _ = std::fs::remove_file(destination);
        let _ = sync_project_directory(project_root);
        return Err(error);
    }

    Ok(())
}

#[cfg(unix)]
fn sync_project_directory(project_root: &Path) -> io::Result<()> {
    std::fs::File::open(project_root)?.sync_all()
}

#[cfg(windows)]
fn commit_local_audio_publication_platform(
    stage: &Path,
    destination: &Path,
    _project_root: &Path,
) -> io::Result<()> {
    use std::{iter, os::windows::ffi::OsStrExt};

    const MOVEFILE_WRITE_THROUGH: u32 = 0x0000_0008;

    #[link(name = "Kernel32")]
    unsafe extern "system" {
        fn MoveFileExW(
            existing_file_name: *const u16,
            new_file_name: *const u16,
            flags: u32,
        ) -> i32;
    }

    let stage_wide: Vec<u16> = stage
        .as_os_str()
        .encode_wide()
        .chain(iter::once(0))
        .collect();
    let destination_wide: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(iter::once(0))
        .collect();

    // SAFETY: both UTF-16 buffers are NUL-terminated and remain alive for the
    // call. No replacement flag is supplied, so an existing destination fails
    // closed. The paths are already restricted to direct children of one
    // app-owned project directory by the public boundary above.
    let moved = unsafe {
        MoveFileExW(
            stage_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_WRITE_THROUGH,
        )
    };
    if moved == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::commit_local_audio_publication;
    use std::{fs, path::PathBuf};

    fn test_root() -> PathBuf {
        std::env::temp_dir().join(format!(
            "bandscope-publication-{}",
            uuid::Uuid::new_v4()
        ))
    }

    #[test]
    fn publication_commits_staged_bytes_without_leaving_the_private_name() {
        let root = test_root();
        fs::create_dir_all(&root).expect("test project root must be created");
        let stage = root.join(".source-test.stage");
        let destination = root.join("source.wav");
        fs::write(&stage, b"bandscope-real-file-boundary")
            .expect("test stage must be written");
        fs::OpenOptions::new()
            .write(true)
            .open(&stage)
            .expect("test stage must reopen")
            .sync_all()
            .expect("test stage must synchronize");

        commit_local_audio_publication(&stage, &destination, &root)
            .expect("publication commit must succeed");

        assert!(!stage.exists(), "private stage name must be removed");
        assert_eq!(
            fs::read(&destination).expect("published bytes must remain readable"),
            b"bandscope-real-file-boundary"
        );
        fs::remove_dir_all(&root).expect("test project root must be removed");
    }

    #[test]
    fn publication_never_clobbers_an_existing_destination() {
        let root = test_root();
        fs::create_dir_all(&root).expect("test project root must be created");
        let stage = root.join(".source-test.stage");
        let destination = root.join("source.wav");
        fs::write(&stage, b"new").expect("test stage must be written");
        fs::write(&destination, b"existing").expect("test destination must be written");

        let error = commit_local_audio_publication(&stage, &destination, &root)
            .expect_err("existing destination must fail closed");

        assert_ne!(error.kind(), io::ErrorKind::InvalidInput);
        assert_eq!(
            fs::read(&destination).expect("existing destination must remain readable"),
            b"existing"
        );
        assert_eq!(
            fs::read(&stage).expect("failed publication must retain its stage"),
            b"new"
        );
        fs::remove_dir_all(&root).expect("test project root must be removed");
    }

    #[test]
    fn publication_rejects_paths_outside_the_project_root() {
        let root = test_root();
        let other = test_root();
        fs::create_dir_all(&root).expect("test project root must be created");
        fs::create_dir_all(&other).expect("other test root must be created");
        let stage = other.join(".source-test.stage");
        let destination = root.join("source.wav");
        fs::write(&stage, b"new").expect("test stage must be written");

        let error = commit_local_audio_publication(&stage, &destination, &root)
            .expect_err("cross-root publication must fail closed");

        assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
        assert!(stage.exists(), "rejected stage must remain untouched");
        assert!(!destination.exists(), "rejected destination must not be created");
        fs::remove_dir_all(&root).expect("test project root must be removed");
        fs::remove_dir_all(&other).expect("other test root must be removed");
    }
}
