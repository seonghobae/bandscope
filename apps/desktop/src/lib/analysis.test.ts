import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDemoAnalysisJobRequest, createDemoRehearsalSong } from "@bandscope/shared-types";
import {
  MAX_LOCAL_AUDIO_FILE_BYTES,
  MAX_YOUTUBE_URL_LENGTH,
  getAnalysisJobStatus,
  importYoutubeUrl,
  selectLocalAudioSource,
  startAnalysisJob
} from "./analysis";

type TauriWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __TAURI_INVOKE__?: unknown;
};

const tauriWindow = window as TauriWindow;
const OVERSIZED_LOCAL_AUDIO_NEXT_ACTION = "Choose a shorter or smaller song file to start analysis.";

describe("analysis bridge", () => {
  beforeEach(() => {
    delete tauriWindow.__TAURI_INTERNALS__;
    delete tauriWindow.__TAURI_INVOKE__;
  });

  it("rejects an oversized native local-audio selection before it becomes project state", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue({
      projectId: "native-local-project",
      sourceMode: "reference",
      projectRoot: "/tmp/bandscope/projects/native-local-project",
      cacheRoot: "/tmp/bandscope/cache/native-local-project",
      tempRoot: "/tmp/bandscope/temp/native-local-project",
      source: {
        sourcePath: "/tmp/bandscope/input.wav",
        fileName: "input.wav",
        extension: "wav",
        fileSizeBytes: MAX_LOCAL_AUDIO_FILE_BYTES + 1
      }
    });

    const selection = await selectLocalAudioSource();

    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: OVERSIZED_LOCAL_AUDIO_NEXT_ACTION
      }
    });
  });

  it("rejects an oversized native YouTube import before it becomes project state", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue({
      projectId: "native-youtube-project",
      sourceMode: "reference",
      projectRoot: "/tmp/bandscope/projects/native-youtube-project",
      cacheRoot: "/tmp/bandscope/cache/native-youtube-project",
      tempRoot: "/tmp/bandscope/temp/native-youtube-project",
      source: {
        sourcePath: "/tmp/bandscope/temp/native-youtube-project/youtube.wav",
        fileName: "youtube.wav",
        extension: "wav",
        fileSizeBytes: MAX_LOCAL_AUDIO_FILE_BYTES + 1
      }
    });

    const selection = await importYoutubeUrl("https://youtu.be/4ozX4yFUC34");

    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: OVERSIZED_LOCAL_AUDIO_NEXT_ACTION
      }
    });
  });

  it("imports a standard YouTube URL through the browser fallback when Tauri is absent", async () => {
    const selection = await importYoutubeUrl("https://www.youtube.com/watch?v=4ozX4yFUC34");

    expect(selection).toEqual({
      ok: true,
      bootstrap: {
        projectId: "browser-youtube-project",
        sourceMode: "reference",
        projectRoot: "browser://bandscope/projects/browser-youtube-project",
        cacheRoot: "browser://bandscope/cache/browser-youtube-project",
        tempRoot: "browser://bandscope/temp/browser-youtube-project",
        source: {
          sourcePath: "browser://bandscope/temp/browser-youtube-project/youtube-preview.m4a",
          fileName: "youtube-preview.m4a",
          extension: "m4a",
          fileSizeBytes: 1
        }
      }
    });
  });

  it("uses the browser fallback when Tauri internals are present but invoke is unavailable", async () => {
    tauriWindow.__TAURI_INTERNALS__ = {};

    const selection = await importYoutubeUrl("https://www.youtube.com/watch?v=4ozX4yFUC34");

    expect(selection.ok).toBe(true);
  });

  it("keeps browser fallback URL intake aligned with the native YouTube allowlist", async () => {
    const selection = await importYoutubeUrl("https://example.com/watch?v=4ozX4yFUC34");

    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: "Only standard YouTube URLs are supported."
      }
    });
  });

  it("rejects non-standard YouTube subdomains before crossing the Tauri bridge", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn();

    const selection = await importYoutubeUrl("https://evil.youtube.com/watch?v=4ozX4yFUC34");

    expect(tauriWindow.__TAURI_INVOKE__).not.toHaveBeenCalled();
    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: "Only standard YouTube URLs are supported."
      }
    });
  });

  it("uses the Tauri v1 invoke shim when it is available", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue({
      projectId: "native-youtube-project",
      sourceMode: "reference",
      projectRoot: "/tmp/bandscope/projects/native-youtube-project",
      cacheRoot: "/tmp/bandscope/cache/native-youtube-project",
      tempRoot: "/tmp/bandscope/temp/native-youtube-project",
      source: {
        sourcePath: "/tmp/bandscope/temp/native-youtube-project/youtube.wav",
        fileName: "youtube.wav",
        extension: "wav",
        fileSizeBytes: 1024
      }
    });

    const selection = await importYoutubeUrl("https://youtu.be/4ozX4yFUC34");

    expect(tauriWindow.__TAURI_INVOKE__).toHaveBeenCalledWith("import_youtube_url", {
      url: "https://youtu.be/4ozX4yFUC34"
    });
    expect(selection.ok).toBe(true);
  });

  it.each([
    "Could not read the selected audio file.",
    "Could not prepare the local project workspace.",
    "Could not prepare the local cache workspace.",
    "Could not prepare the local temp workspace."
  ])("preserves an approved native local-audio string error: %s", async (message) => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockRejectedValue(message);

    await expect(selectLocalAudioSource()).resolves.toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message
      }
    });
  });

  it("redacts an unapproved native local-audio string error", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi
      .fn()
      .mockRejectedValue("Could not read /Users/example/Music/private-demo.wav");

    await expect(selectLocalAudioSource()).resolves.toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: "Choose a WAV, MP3, FLAC, or M4A file to start analysis."
      }
    });
  });

  it("normalizes legacy analysis job status responses before returning them", async () => {
    const legacyResult = createDemoRehearsalSong() as unknown as {
      sections: Array<Record<string, unknown>>;
    };
    delete legacyResult.sections[0]!.timeRange;
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue({
      jobId: "job-legacy",
      state: "succeeded",
      requestedAt: "2026-03-12T00:00:00.000Z",
      updatedAt: "2026-03-12T00:00:00.000Z",
      result: legacyResult
    });

    const status = await getAnalysisJobStatus("job-legacy");

    expect(status.result?.sections[0]?.timeRange).toEqual({ start: 0, end: 1 });
  });

  it("reports staged browser fallback progress before returning the demo result", async () => {
    const queued = await startAnalysisJob(createDemoAnalysisJobRequest());

    expect(queued).toMatchObject({
      state: "queued",
      progressLabel: "Queued for analysis",
      progressStage: "queued",
      progressPercent: 0
    });

    const running = await getAnalysisJobStatus(queued.jobId);
    expect(running).toMatchObject({
      state: "running",
      progressLabel: "Decoding audio",
      progressStage: "decode",
      progressPercent: 20
    });

    expect(await getAnalysisJobStatus(queued.jobId)).toMatchObject({
      state: "running",
      progressLabel: "Separating stems... (45%)",
      progressStage: "separate",
      progressPercent: 45
    });
    expect(await getAnalysisJobStatus(queued.jobId)).toMatchObject({
      state: "running",
      progressLabel: "Building rehearsal cues",
      progressStage: "analyze",
      progressPercent: 70
    });
    expect(await getAnalysisJobStatus(queued.jobId)).toMatchObject({
      state: "running",
      progressLabel: "Saving reusable features",
      progressStage: "persist",
      progressPercent: 90
    });

    const ready = await getAnalysisJobStatus(queued.jobId);
    expect(ready).toMatchObject({
      state: "succeeded",
      progressLabel: "Analysis ready",
      progressStage: "ready",
      progressPercent: 100
    });
  });

  it("ignores a non-function Tauri v1 invoke shim", async () => {
    (window as unknown as { __TAURI_INVOKE__?: unknown }).__TAURI_INVOKE__ = "not-callable";

    const selection = await importYoutubeUrl("https://youtu.be/4ozX4yFUC34");

    expect(selection.ok).toBe(true);
  });

  it("rejects unsupported YouTube URLs before crossing the Tauri bridge", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn();

    const selection = await importYoutubeUrl("http://youtube.com/watch?v=4ozX4yFUC34");

    expect(tauriWindow.__TAURI_INVOKE__).not.toHaveBeenCalled();
    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: "Only standard YouTube URLs are supported."
      }
    });
  });

  it("rejects duplicate YouTube video identifiers before crossing the Tauri bridge", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn();

    const selection = await importYoutubeUrl("https://youtube.com/watch?v=4ozX4yFUC34&v=");

    expect(tauriWindow.__TAURI_INVOKE__).not.toHaveBeenCalled();
    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: "Only standard YouTube URLs are supported."
      }
    });
  });

  it("rejects oversized YouTube URLs before crossing the Tauri bridge", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn();
    const urlPrefix = "https://youtube.com/watch?v=4ozX4yFUC34&x=";
    const oversizedUrl = `${urlPrefix}${"a".repeat(MAX_YOUTUBE_URL_LENGTH - urlPrefix.length + 1)}`;

    const selection = await importYoutubeUrl(oversizedUrl);

    expect(tauriWindow.__TAURI_INVOKE__).not.toHaveBeenCalled();
    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: "Only standard YouTube URLs are supported."
      }
    });
  });

  it.each([
    "https://youtube.com/watch?v=too-short",
    "https://youtube.com/watch?v=4ozX4yFUC3!",
    "https://youtu.be/too-short",
    "https://youtu.be/4ozX4yFUC3!"
  ])("rejects malformed YouTube video identifiers before crossing the Tauri bridge", async (url) => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn();

    const selection = await importYoutubeUrl(url);

    expect(tauriWindow.__TAURI_INVOKE__).not.toHaveBeenCalled();
    expect(selection).toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: "Only standard YouTube URLs are supported."
      }
    });
  });
});
