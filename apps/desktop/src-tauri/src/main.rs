#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod local_audio_publication;

use bandscope_desktop_core::*;
use local_audio_publication::commit_local_audio_publication;
use rfd::FileDialog;
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{atomic::Ordering, mpsc},
    thread,
    time::Instant,
};
use tauri::{Emitter, Manager, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

/// Native-only cache of verified local-audio publication identities.
///
/// Security Notes: entries are keyed only by BandScope-minted project ids and
/// contain the bounded path-free publication evidence emitted by Resource
/// Admission. User filesystem paths are never retained in this state.
#[derive(Default)]
struct LocalAudioPublicationIdentityState(
    std::sync::Mutex<std::collections::HashMap<String, LocalAudioPublicationIdentity>>,
);

fn iso_timestamp_now() -> String {
    OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_else(|_| "1970-01-01T00:00:00Z".into())
}

fn unique_push(paths: &mut Vec<PathBuf>, candidate: PathBuf) {
    if !paths.iter().any(|existing| existing == &candidate) {
        paths.push(candidate);
    }
}

fn runtime_search_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(parent) = current_exe.parent() {
            unique_push(&mut roots, parent.to_path_buf());
            unique_push(&mut roots, parent.join("resources"));
            unique_push(&mut roots, parent.join("../Resources"));
        }
    }
    roots
}

fn analysis_command() -> (PathBuf, String, Vec<String>) {
    if let Ok(python_path) = std::env::var("BANDSCOPE_ANALYSIS_PYTHON") {
        return (
            std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
            python_path,
            vec!["-m".into(), "bandscope_analysis.cli".into()],
        );
    }

    for root in runtime_search_roots() {
        let candidates = [
            root.join("services")
                .join("analysis-engine")
                .join(".venv")
                .join("bin")
                .join("python"),
            root.join("services")
                .join("analysis-engine")
                .join(".venv")
                .join("Scripts")
                .join("python.exe"),
            root.join("analysis-engine")
                .join(".venv")
                .join("bin")
                .join("python"),
            root.join("analysis-engine")
                .join(".venv")
                .join("Scripts")
                .join("python.exe"),
            root.join("analysis-engine")
                .join("python")
                .join("bin")
                .join("python"),
            root.join("analysis-engine")
                .join("python")
                .join("python.exe"),
            root.join("python").join("bin").join("python"),
            root.join("python").join("python.exe"),
        ];

        for candidate in candidates {
            if candidate.is_file() {
                return (
                    root,
                    candidate.to_string_lossy().into_owned(),
                    vec!["-m".into(), "bandscope_analysis.cli".into()],
                );
            }
        }
    }

    (
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
        MISSING_ANALYSIS_PYTHON.into(),
        Vec::new(),
    )
}

fn try_acquire_job_slot(state: &AppState) -> bool {
    state
        .0
        .in_flight_jobs
        .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
            (current < MAX_IN_FLIGHT_JOBS).then_some(current + 1)
        })
        .is_ok()
}

fn release_job_slot(state: &AppState) {
    state.0.in_flight_jobs.fetch_sub(1, Ordering::SeqCst);
}

fn app_owned_root<R: Runtime>(
    app: &tauri::AppHandle<R>,
    kind: &str,
    project_id: &str,
) -> Result<PathBuf, String> {
    if !is_valid_project_id(project_id) {
        return Err("Invalid project ID: path traversal detected.".to_string());
    }

    let base_root = match kind {
        "projects" => app
            .path()
            .app_local_data_dir()
            .map_err(|_| "Could not prepare the local project workspace.".to_string())?,
        "cache" => app
            .path()
            .app_cache_dir()
            .map_err(|_| "Could not prepare the local cache workspace.".to_string())?,
        "temp" => app
            .path()
            .app_local_data_dir()
            .map(|path| path.join("temp"))
            .map_err(|_| "Could not prepare the local temp workspace.".to_string())?,
        _ => return Err(format!("Could not prepare the local {kind} workspace.")),
    };
    let root = base_root.join(project_id);
    std::fs::create_dir_all(&root)
        .map_err(|_| format!("Could not prepare the local {kind} workspace."))?;
    Ok(root)
}

/// Admit one OS-selected local audio file into a project-owned immutable source artifact.
///
/// Security Notes: the external path is used only to canonicalize and open the
/// user-authorized source. Size is checked from that opened descriptor, bytes
/// are copied through the bounded Resource Admission helper into a private
/// same-project staging file. After the stage is synchronized, publication uses
/// a platform-specific no-clobber durability boundary: Unix links the stage,
/// removes the private name, and synchronizes the project directory; Windows
/// performs a no-replace `MoveFileExW` with `MOVEFILE_WRITE_THROUGH`. Only then
/// is the published object re-opened and required to reproduce the staging
/// size+SHA-256 receipt before path-free bootstrap/persistence identity is
/// minted. This does not claim durability for creation or replacement of
/// higher directory ancestors. Atomic no-follow descriptor acquisition remains
/// a separate platform-hardening requirement.
fn materialize_local_audio_source(
    path: &Path,
    project_root: &Path,
    project_id: &str,
) -> Result<(LocalAudioSourcePayload, LocalAudioPublicationIdentity), String> {
    let canonical = path
        .canonicalize()
        .map_err(|_| "Could not read the selected audio file.".to_string())?;
    let extension = canonical
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| value.to_ascii_lowercase())
        .ok_or_else(|| "Choose a WAV, MP3, FLAC, or M4A file to start analysis.".to_string())?;
    if !AUDIO_EXTENSIONS.contains(&extension.as_str()) {
        return Err("Choose a WAV, MP3, FLAC, or M4A file to start analysis.".into());
    }
    let file_name = canonical
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "Could not read the selected audio file.".to_string())?
        .to_string();
    let source = std::fs::File::open(&canonical)
        .map_err(|_| "Could not read the selected audio file.".to_string())?;
    let metadata = source
        .metadata()
        .map_err(|_| "Could not read the selected audio file.".to_string())?;
    if !metadata.is_file() {
        return Err("Could not read the selected audio file.".into());
    }
    validate_local_audio_file_size(metadata.len())?;

    let destination = project_root.join(format!("source.{extension}"));
    let stage = project_root.join(format!(".source-{}.stage", uuid::Uuid::new_v4()));
    let mut staged = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&stage)
        .map_err(|_| "Could not prepare the local project workspace.".to_string())?;

    let receipt = match copy_bounded_local_audio_with_receipt(source, &mut staged) {
        Ok(receipt) => receipt,
        Err(error) => {
            drop(staged);
            let _ = std::fs::remove_file(&stage);
            return Err(error);
        }
    };
    if staged.sync_all().is_err() {
        drop(staged);
        let _ = std::fs::remove_file(&stage);
        return Err("Could not prepare the local project workspace.".to_string());
    }
    drop(staged);

    if commit_local_audio_publication(&stage, &destination, project_root).is_err() {
        let _ = std::fs::remove_file(&stage);
        return Err("Could not prepare the local project workspace.".to_string());
    }

    let published_path_metadata = match std::fs::symlink_metadata(&destination) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        _ => {
            let _ = std::fs::remove_file(&destination);
            return Err("Could not prepare the local project workspace.".to_string());
        }
    };
    if published_path_metadata.len() != receipt.file_size_bytes {
        let _ = std::fs::remove_file(&destination);
        return Err("Could not prepare the local project workspace.".to_string());
    }
    let published = match std::fs::File::open(&destination) {
        Ok(file) => file,
        Err(_) => {
            let _ = std::fs::remove_file(&destination);
            return Err("Could not prepare the local project workspace.".to_string());
        }
    };
    let published_descriptor_metadata = match published.metadata() {
        Ok(metadata) if metadata.is_file() && metadata.len() == receipt.file_size_bytes => metadata,
        _ => {
            drop(published);
            let _ = std::fs::remove_file(&destination);
            return Err("Could not prepare the local project workspace.".to_string());
        }
    };
    if published_descriptor_metadata.len() != published_path_metadata.len()
        || verify_local_audio_publication_receipt(published, &receipt).is_err()
    {
        let _ = std::fs::remove_file(&destination);
        return Err("Could not prepare the local project workspace.".to_string());
    }
    let published_path_metadata = match std::fs::symlink_metadata(&destination) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        _ => {
            let _ = std::fs::remove_file(&destination);
            return Err("Could not prepare the local project workspace.".to_string());
        }
    };
    if published_path_metadata.len() != receipt.file_size_bytes {
        let _ = std::fs::remove_file(&destination);
        return Err("Could not prepare the local project workspace.".to_string());
    }

    let publication_identity =
        build_local_audio_publication_identity(project_id, &extension, &receipt)?;
    Ok((
        LocalAudioSourcePayload {
            source_path: destination.to_string_lossy().into_owned(),
            file_name,
            extension,
            file_size_bytes: receipt.file_size_bytes,
        },
        publication_identity,
    ))
}

fn parse_request_payload(payload: Value) -> Result<AnalysisJobRequest, String> {
    let Value::Object(map) = payload else {
        return Err("Invalid analysis job request: invalid field 'root'".into());
    };

    for key in map.keys() {
        if key != "sourceKind"
            && key != "projectId"
            && key != "sourceLabel"
            && key != "roleFocus"
            && key != "localSource"
        {
            return Err(format!(
                "Invalid analysis job request: invalid field '{key}'"
            ));
        }
    }

    let source_kind = map.get("sourceKind").and_then(Value::as_str);
    let project_id = map.get("projectId").and_then(Value::as_str);
    let source_label = map.get("sourceLabel").and_then(Value::as_str);
    let role_focus = map.get("roleFocus").and_then(Value::as_array);
    let local_source = match map.get("localSource") {
        Some(value) => Some(
            serde_json::from_value::<LocalAudioSourcePayload>(value.clone()).map_err(|_| {
                "Invalid analysis job request: invalid field 'localSource'".to_string()
            })?,
        ),
        None => None,
    };

    if source_kind != Some("demo") && source_kind != Some("local_audio") {
        return Err("Invalid analysis job request: invalid field 'sourceKind'".into());
    }
    let source_label = source_label
        .filter(|label| !label.trim().is_empty())
        .ok_or_else(|| "Invalid analysis job request: invalid field 'sourceLabel'".to_string())?;
    let role_focus = role_focus
        .ok_or_else(|| "Invalid analysis job request: invalid field 'roleFocus'".to_string())?;
    let mut parsed_role_focus = Vec::with_capacity(role_focus.len());
    for (index, role) in role_focus.iter().enumerate() {
        let Some(role) = role.as_str() else {
            return Err(format!(
                "Invalid analysis job request: invalid field 'roleFocus[{index}]'"
            ));
        };
        parsed_role_focus.push(role.to_string());
    }

    match source_kind {
        Some("demo") => {
            if local_source.is_some() || project_id.is_some() {
                return Err("Invalid analysis job request: invalid field 'projectId'".into());
            }
        }
        Some("local_audio") => {
            let Some(project_id) = project_id else {
                return Err("Invalid analysis job request: invalid field 'projectId'".into());
            };
            if !is_valid_project_id(project_id) {
                return Err("Invalid analysis job request: invalid field 'projectId'".to_string());
            }
            if local_source.is_some() {
                return Err("Invalid analysis job request: invalid field 'localSource'".into());
            }
            return Ok(AnalysisJobRequest {
                source_kind: "local_audio".to_string(),
                project_id: Some(project_id.to_string()),
                source_label: source_label.to_string(),
                role_focus: parsed_role_focus,
                local_source,
                cache_root: None,
                temp_root: None,
            });
        }
        _ => {}
    }

    Ok(AnalysisJobRequest {
        source_kind: source_kind.unwrap_or("demo").to_string(),
        project_id: None,
        source_label: source_label.to_string(),
        role_focus: parsed_role_focus,
        local_source,
        cache_root: None,
        temp_root: None,
    })
}

fn failed_status(
    job_id: String,
    requested_at: String,
    code: AnalysisJobErrorCode,
    message: &str,
) -> AnalysisJobStatus {
    AnalysisJobStatus {
        job_id,
        state: AnalysisJobState::Failed,
        requested_at,
        updated_at: iso_timestamp_now(),
        progress_label: None,
        progress_stage: None,
        progress_percent: None,
        cache_status: None,
        result: None,
        error: Some(AnalysisJobError {
            code,
            message: message.into(),
        }),
    }
}

fn store_status(state: &AppState, status: &AnalysisJobStatus) {
    if let Ok(mut jobs) = state.0.jobs.lock() {
        jobs.insert(status.job_id.clone(), status.clone());
    }
}

fn store_status_and_emit<R: Runtime>(
    state: &AppState,
    app: &tauri::AppHandle<R>,
    status: &AnalysisJobStatus,
) {
    store_status(state, status);
    let _ = app.emit("analysis-job-updated", status);
}

fn store_bootstrap_source(state: &AppState, summary: ProjectBootstrapSummaryPayload) {
    if let Ok(mut sources) = state.0.bootstrap_sources.lock() {
        sources.insert(summary.project_id.clone(), summary);
    }
}

/// Retain path-free publication evidence before the renderer receives bootstrap authority.
fn store_local_audio_publication_identity(
    state: &LocalAudioPublicationIdentityState,
    identity: LocalAudioPublicationIdentity,
) -> Result<(), String> {
    let project_id = identity.project_id.clone();
    let mut identities = state
        .0
        .lock()
        .map_err(|_| "Could not prepare the local project workspace.".to_string())?;
    identities.insert(project_id, identity);
    Ok(())
}

fn lookup_bootstrap_source(
    state: &AppState,
    project_id: &str,
) -> Result<ProjectBootstrapSummaryPayload, String> {
    state
        .0
        .bootstrap_sources
        .lock()
        .ok()
        .and_then(|sources| sources.get(project_id).cloned())
        .ok_or_else(|| "Analysis job source was not found. Choose local audio again.".to_string())
}

fn drain_analysis_status_updates(
    state: &AppState,
    app: &tauri::AppHandle<impl Runtime>,
    status_rx: &mpsc::Receiver<AnalysisJobStatus>,
    last_status: &mut Option<AnalysisJobStatus>,
) {
    while let Ok(status) = status_rx.try_recv() {
        store_status_and_emit(state, app, &status);
        *last_status = Some(status);
    }
}

fn run_analysis_engine(
    state: AppState,
    app: tauri::AppHandle<impl Runtime>,
    job_id: String,
    request: AnalysisJobRequest,
    requested_at: String,
) -> AnalysisJobStatus {
    let (working_dir, program, mut args) = analysis_command();

    if program == MISSING_ANALYSIS_PYTHON {
        return failed_status(
            job_id,
            requested_at,
            AnalysisJobErrorCode::EngineUnavailable,
            "Analysis engine is unavailable.",
        );
    }
    args.push("--progress-jsonl".into());

    let mut process = match Command::new(program)
        .args(args)
        .current_dir(working_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(process) => process,
        Err(_) => {
            return failed_status(
                job_id,
                requested_at,
                AnalysisJobErrorCode::EngineUnavailable,
                "Analysis engine is unavailable.",
            )
        }
    };

    let payload = json!({
        "jobId": job_id.clone(),
        "request": request,
    });
    let Some(stdout) = process.stdout.take() else {
        let _ = process.kill();
        let _ = process.wait();
        return failed_status(
            job_id,
            requested_at,
            AnalysisJobErrorCode::EngineUnavailable,
            "Analysis engine is unavailable.",
        );
    };
    let Some(stderr) = process.stderr.take() else {
        let _ = process.kill();
        let _ = process.wait();
        return failed_status(
            job_id,
            requested_at,
            AnalysisJobErrorCode::EngineUnavailable,
            "Analysis engine is unavailable.",
        );
    };
    let (status_tx, status_rx) = mpsc::channel::<AnalysisJobStatus>();
    let stdout_reader = thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut last_status = None;
        for line in reader.lines() {
            let Ok(line) = line else {
                break;
            };
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            if let Ok(status) = serde_json::from_str::<AnalysisJobStatus>(trimmed) {
                last_status = Some(status.clone());
                if status_tx.send(status).is_err() {
                    break;
                }
            }
        }
        last_status
    });
    let stderr_reader = thread::spawn(move || {
        let mut reader = stderr;
        let mut buffer = Vec::new();
        let _ = reader.read_to_end(&mut buffer);
        buffer
    });

    if let Some(mut stdin) = process.stdin.take() {
        if stdin.write_all(payload.to_string().as_bytes()).is_err() {
            let _ = process.kill();
            let _ = process.wait();
            let _ = stdout_reader.join();
            let _ = stderr_reader.join();
            return failed_status(
                payload["jobId"]
                    .as_str()
                    .unwrap_or("unknown-job")
                    .to_string(),
                requested_at,
                AnalysisJobErrorCode::EngineUnavailable,
                "Analysis engine is unavailable.",
            );
        }
    }

    let deadline = Instant::now() + ANALYSIS_PROCESS_TIMEOUT;
    let mut last_status = None;
    let exit_status;
    loop {
        drain_analysis_status_updates(&state, &app, &status_rx, &mut last_status);
        match process.try_wait() {
            Ok(Some(status)) => {
                exit_status = status;
                break;
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = process.kill();
                    let _ = process.wait();
                    let _ = stdout_reader.join();
                    let _ = stderr_reader.join();
                    return failed_status(
                        payload["jobId"]
                            .as_str()
                            .unwrap_or("unknown-job")
                            .to_string(),
                        requested_at,
                        AnalysisJobErrorCode::EngineUnavailable,
                        "Analysis engine timed out.",
                    );
                }
                thread::sleep(ANALYSIS_WAIT_POLL);
            }
            Err(_) => {
                let _ = process.kill();
                let _ = process.wait();
                let _ = stdout_reader.join();
                let _ = stderr_reader.join();
                return failed_status(
                    payload["jobId"]
                        .as_str()
                        .unwrap_or("unknown-job")
                        .to_string(),
                    requested_at,
                    AnalysisJobErrorCode::EngineUnavailable,
                    "Analysis engine is unavailable.",
                );
            }
        }
    }
    let reader_last_status = stdout_reader.join().unwrap_or(None);
    let _ = stderr_reader.join();
    drain_analysis_status_updates(&state, &app, &status_rx, &mut last_status);
    if last_status.is_none() {
        last_status = reader_last_status;
    }

    if !exit_status.success() {
        return failed_status(
            payload["jobId"]
                .as_str()
                .unwrap_or("unknown-job")
                .to_string(),
            requested_at,
            AnalysisJobErrorCode::EngineUnavailable,
            "Analysis engine is unavailable.",
        );
    }

    last_status.unwrap_or_else(|| {
        failed_status(
            payload["jobId"]
                .as_str()
                .unwrap_or("unknown-job")
                .to_string(),
            requested_at,
            AnalysisJobErrorCode::EngineUnavailable,
            "Analysis engine returned an invalid response.",
        )
    })
}

#[tauri::command]
fn start_analysis_job(
    request: Value,
    app: tauri::AppHandle<impl Runtime>,
    state: tauri::State<'_, AppState>,
) -> AnalysisJobStatus {
    let requested_at = iso_timestamp_now();
    let mut parsed_request = match parse_request_payload(request) {
        Ok(parsed_request) => parsed_request,
        Err(message) => {
            return failed_status(
                "invalid-job".into(),
                requested_at,
                AnalysisJobErrorCode::InvalidRequest,
                &message,
            )
        }
    };

    if parsed_request.source_kind == "local_audio" {
        let Some(project_id) = parsed_request.project_id.clone() else {
            return failed_status(
                "invalid-job".into(),
                requested_at,
                AnalysisJobErrorCode::InvalidRequest,
                "Invalid analysis job request: invalid field 'projectId'",
            );
        };
        let bootstrap = match lookup_bootstrap_source(&state, &project_id) {
            Ok(bootstrap) => bootstrap,
            Err(message) => {
                return failed_status(
                    "invalid-job".into(),
                    requested_at,
                    AnalysisJobErrorCode::NotFound,
                    &message,
                )
            }
        };
        parsed_request.source_label = bootstrap.source.file_name.clone();
        parsed_request.cache_root = Some(bootstrap.cache_root.clone());
        parsed_request.temp_root = Some(bootstrap.temp_root.clone());
        parsed_request.local_source = Some(bootstrap.source);
    }

    let job_id = format!("job-{}", state.0.next_job.fetch_add(1, Ordering::Relaxed));
    if !try_acquire_job_slot(&state) {
        return failed_status(
            job_id,
            requested_at,
            AnalysisJobErrorCode::EngineUnavailable,
            "Analysis queue is full. Please wait for a running job to finish.",
        );
    }
    let queued = AnalysisJobStatus {
        job_id: job_id.clone(),
        state: AnalysisJobState::Queued,
        requested_at: requested_at.clone(),
        updated_at: requested_at.clone(),
        progress_label: Some("Queued for analysis".into()),
        progress_stage: Some(AnalysisJobStage::Queued),
        progress_percent: Some(0),
        cache_status: Some(AnalysisCacheStatus::Disabled),
        result: None,
        error: None,
    };
    store_status_and_emit(&state, &app, &queued);

    let app_state = state.inner().clone();
    let worker_app_handle = app.clone();
    std::thread::spawn(move || {
        store_status_and_emit(
            &app_state,
            &worker_app_handle,
            &AnalysisJobStatus {
                job_id: job_id.clone(),
                state: AnalysisJobState::Running,
                requested_at: requested_at.clone(),
                updated_at: iso_timestamp_now(),
                progress_label: Some("Running analysis".into()),
                progress_stage: Some(AnalysisJobStage::Decode),
                progress_percent: Some(10),
                cache_status: None,
                result: None,
                error: None,
            },
        );
        let finished = run_analysis_engine(
            app_state.clone(),
            worker_app_handle.clone(),
            job_id,
            parsed_request,
            requested_at,
        );
        store_status_and_emit(&app_state, &worker_app_handle, &finished);
        release_job_slot(&app_state);
    });

    queued
}

#[tauri::command]
fn get_analysis_job_status(job_id: String, state: tauri::State<'_, AppState>) -> AnalysisJobStatus {
    state
        .0
        .jobs
        .lock()
        .ok()
        .and_then(|jobs| jobs.get(&job_id).cloned())
        .unwrap_or_else(|| {
            failed_status(
                job_id,
                iso_timestamp_now(),
                AnalysisJobErrorCode::NotFound,
                "Analysis job was not found.",
            )
        })
}

#[tauri::command]
fn select_local_audio_source(
    app: tauri::AppHandle<impl Runtime>,
    state: tauri::State<'_, AppState>,
    publication_state: tauri::State<'_, LocalAudioPublicationIdentityState>,
) -> Result<ProjectBootstrapSummaryPayload, String> {
    let path = FileDialog::new()
        .add_filter("Audio", &AUDIO_EXTENSIONS)
        .pick_file()
        .ok_or_else(|| "Choose a WAV, MP3, FLAC, or M4A file to start analysis.".to_string())?;
    let project_id = next_project_id(&state);
    let project_root = app_owned_root(&app, "projects", &project_id)?;
    let cache_root = app_owned_root(&app, "cache", &project_id)?;
    let temp_root = app_owned_root(&app, "temp", &project_id)?;
    let (source, publication_identity) =
        materialize_local_audio_source(&path, &project_root, &project_id)?;
    store_local_audio_publication_identity(&publication_state, publication_identity)?;

    let summary = ProjectBootstrapSummaryPayload {
        project_id,
        source_mode: "reference".into(),
        project_root: project_root.to_string_lossy().into_owned(),
        cache_root: cache_root.to_string_lossy().into_owned(),
        temp_root: temp_root.to_string_lossy().into_owned(),
        source,
    };
    store_bootstrap_source(&state, summary.clone());

    Ok(summary)
}

#[tauri::command]
async fn import_youtube_url(
    url: String,
    app: tauri::AppHandle<impl Runtime>,
    state: tauri::State<'_, AppState>,
) -> Result<ProjectBootstrapSummaryPayload, String> {
    if !is_supported_youtube_url(&url) {
        return Err("Only standard YouTube URLs are supported.".to_string());
    }

    let project_id = next_project_id(&state);
    let project_root = app_owned_root(&app, "projects", &project_id)?;
    let cache_root = app_owned_root(&app, "cache", &project_id)?;
    let temp_root = app_owned_root(&app, "temp", &project_id)?;

    let (working_dir, program, mut args) = analysis_command();
    if program == MISSING_ANALYSIS_PYTHON {
        return Err("Analysis engine is unavailable.".to_string());
    }

    // Replace `bandscope_analysis.cli` with `bandscope_analysis.youtube`
    if let Some(pos) = args.iter().position(|a| a == "bandscope_analysis.cli") {
        args[pos] = "bandscope_analysis.youtube".into();
    } else {
        return Err("Internal error: Could not construct YouTube import command.".to_string());
    }
    args.push("--url".into());
    args.push(url.clone());
    args.push("--out-dir".into());
    args.push(cache_root.to_string_lossy().into_owned());

    let output = tauri::async_runtime::spawn_blocking(move || {
        let mut command = Command::new(program);
        command.args(args).current_dir(working_dir);
        wait_for_process_output(
            command,
            YOUTUBE_IMPORT_TIMEOUT,
            ANALYSIS_WAIT_POLL,
            "YouTube import timed out.",
        )
    })
    .await
    .map_err(|_| "Failed to execute YouTube import process.".to_string())??;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: serde_json::Value = serde_json::from_str(&stdout)
        .map_err(|_| "Failed to parse YouTube import response.".to_string())?;

    if parsed.get("ok").and_then(|v| v.as_bool()) == Some(true) {
        if let Some(metadata) = parsed.get("metadata") {
            let source = youtube_source_from_metadata(metadata, &cache_root)?;
            validate_local_audio_file_size(source.file_size_bytes)?;

            let summary = ProjectBootstrapSummaryPayload {
                project_id,
                source_mode: "reference".into(),
                project_root: project_root.to_string_lossy().into_owned(),
                cache_root: cache_root.to_string_lossy().into_owned(),
                temp_root: temp_root.to_string_lossy().into_owned(),
                source,
            };
            store_bootstrap_source(&state, summary.clone());
            return Ok(summary);
        }
        return Err(youtube_missing_metadata_error(&parsed));
    }

    if let Some(err) = parsed.get("error") {
        let msg = err
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error during YouTube import.");
        return Err(msg.to_string());
    }

    Err("YouTube import failed with an unknown error.".to_string())
}

#[tauri::command]
fn save_project(payload: Value) -> Result<(), String> {
    let parsed = serde_json::from_value::<RehearsalSongPayload>(payload)
        .map_err(|_| "Invalid project payload".to_string())?;

    let path = FileDialog::new()
        .add_filter("BandScope Project", &["bscope", "json"])
        .save_file()
        .ok_or_else(|| "User cancelled".to_string())?;

    let content = serde_json::to_string_pretty(&parsed)
        .map_err(|_| "Failed to serialize project".to_string())?;
    std::fs::write(path, content).map_err(|_| "Failed to write file".to_string())?;

    Ok(())
}

#[tauri::command]
fn load_project() -> Result<RehearsalSongPayload, String> {
    let path = FileDialog::new()
        .add_filter("BandScope Project", &["bscope", "json"])
        .pick_file()
        .ok_or_else(|| "User cancelled".to_string())?;

    let metadata = std::fs::metadata(&path).map_err(|_| "Failed to read file".to_string())?;
    if metadata.len() > 5 * 1024 * 1024 {
        return Err("Project file is too large (exceeds 5MB limit)".to_string());
    }

    let content = std::fs::read_to_string(path).map_err(|_| "Failed to read file".to_string())?;
    project_payload_from_content(&content)
}

fn scores_root_for_project<R: Runtime>(
    app: &tauri::AppHandle<R>,
    project_id: &str,
) -> Result<PathBuf, String> {
    // Callers must have validated `project_id` with `is_valid_project_id`
    // before this join; the root stays inside the app-owned data directory.
    let project_root = app_owned_root(app, "projects", project_id)?;
    let root = project_root.join("scores");
    std::fs::create_dir_all(&root)
        .map_err(|_| "Could not prepare the local scores workspace.".to_string())?;
    Ok(root)
}

/// Security Notes: the file path comes exclusively from the OS file dialog
/// (never from JS), is validated (magic bytes, size, extension, no symlink),
/// and is copied into the app-owned scores directory. The stored copy is named
/// by a locally minted UUID v4, so no untrusted external path is ever
/// referenced again after this command returns.
#[tauri::command]
fn attach_score_pdf(
    project_id: String,
    song_id: String,
    app: tauri::AppHandle<impl Runtime>,
) -> Result<ScoreAttachmentPayload, String> {
    if !is_valid_project_id(&project_id) {
        return Err("Invalid project id.".to_string());
    }
    // `song_id` is part of the viewer contract (score-to-song association is
    // persisted on the JS side in a later slice); it never touches a path.
    if song_id.trim().is_empty() {
        return Err("Invalid song id.".to_string());
    }

    let path = FileDialog::new()
        .add_filter("PDF Score", &["pdf"])
        .pick_file()
        .ok_or_else(|| "Choose a PDF file to attach as a score.".to_string())?;
    let (source, file_name, file_size_bytes) = validate_score_pdf_source(&path)?;

    let scores_root = scores_root_for_project(&app, &project_id)?;
    let score_id = uuid::Uuid::new_v4().to_string();
    let destination = scores_root.join(format!("{score_id}.pdf"));
    std::fs::copy(&source, &destination)
        .map_err(|_| "Could not copy the PDF into the project workspace.".to_string())?;

    Ok(ScoreAttachmentPayload {
        score_id,
        file_name,
        file_size_bytes,
    })
}

/// Security Notes: no path crosses the IPC boundary. Both ids are validated
/// against strict allowlist shapes, the path is rebuilt locally, and the
/// canonicalize-plus-prefix guard in `resolve_existing_score_pdf` rejects any
/// escape from the app-owned scores root. The resolved file is then read
/// through the bounded core helper so growth after attachment cannot trigger
/// an allocation beyond the 25 MiB product limit.
#[tauri::command]
fn read_score_pdf(
    project_id: String,
    score_id: String,
    app: tauri::AppHandle<impl Runtime>,
) -> Result<Vec<u8>, String> {
    if !is_valid_project_id(&project_id) {
        return Err("Invalid project id.".to_string());
    }
    let scores_root = scores_root_for_project(&app, &project_id)?;
    let path = resolve_existing_score_pdf(&scores_root, &score_id)?;
    read_validated_score_pdf(&path)
}

/// Security Notes: same id validation and traversal guard as `read_score_pdf`;
/// deletion is scoped to a single validated file inside the app-owned scores
/// root. Returns `false` when the score does not exist (idempotent removal).
#[tauri::command]
fn remove_score_pdf(
    project_id: String,
    score_id: String,
    app: tauri::AppHandle<impl Runtime>,
) -> Result<bool, String> {
    if !is_valid_project_id(&project_id) {
        return Err("Invalid project id.".to_string());
    }
    if !is_valid_score_id(&score_id) {
        return Err("Invalid score id.".to_string());
    }
    let scores_root = scores_root_for_project(&app, &project_id)?;
    let path = match resolve_existing_score_pdf(&scores_root, &score_id) {
        Ok(path) => path,
        Err(_) => return Ok(false),
    };
    std::fs::remove_file(path).map_err(|_| "Could not remove the score PDF.".to_string())?;
    Ok(true)
}

fn main() {
    tauri::Builder::default()
        .manage(AppState::default())
        .manage(LocalAudioPublicationIdentityState::default())
        .invoke_handler(tauri::generate_handler![
            select_local_audio_source,
            import_youtube_url,
            start_analysis_job,
            get_analysis_job_status,
            save_project,
            load_project,
            attach_score_pdf,
            read_score_pdf,
            remove_score_pdf
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
