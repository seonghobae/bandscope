import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  MAX_LOCAL_AUDIO_FILE_BYTES,
  importYoutubeUrl,
  selectLocalAudioSource
} from "./analysis";

type TauriWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __TAURI_INVOKE__?: unknown;
};

const tauriWindow = window as TauriWindow;
const NEXT_ACTION = "Choose a shorter or smaller song file to start analysis.";

function oversizedBootstrap(projectId: string) {
  return {
    projectId,
    sourceMode: "reference",
    projectRoot: `/tmp/bandscope/projects/${projectId}`,
    cacheRoot: `/tmp/bandscope/cache/${projectId}`,
    tempRoot: `/tmp/bandscope/temp/${projectId}`,
    source: {
      sourcePath: `/tmp/bandscope/${projectId}/input.wav`,
      fileName: "input.wav",
      extension: "wav",
      fileSizeBytes: MAX_LOCAL_AUDIO_FILE_BYTES + 1
    }
  };
}

describe("audio resource rejection next action", () => {
  beforeEach(() => {
    delete tauriWindow.__TAURI_INTERNALS__;
    delete tauriWindow.__TAURI_INVOKE__;
  });

  it("names the next action for an oversized local selection", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue(oversizedBootstrap("local-project"));

    await expect(selectLocalAudioSource()).resolves.toEqual({
      ok: false,
      error: { code: "invalid_request", message: NEXT_ACTION }
    });
  });

  it("names the same next action for an oversized imported selection", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue(oversizedBootstrap("youtube-project"));

    await expect(importYoutubeUrl("https://youtu.be/4ozX4yFUC34")).resolves.toEqual({
      ok: false,
      error: { code: "invalid_request", message: NEXT_ACTION }
    });
  });
});
