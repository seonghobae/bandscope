import { beforeEach, describe, expect, it, vi } from "vitest";
import { importYoutubeUrl, selectLocalAudioSource } from "./analysis";

type TauriWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __TAURI_INVOKE__?: unknown;
};

const tauriWindow = window as TauriWindow;
const INVALID_RESOURCE_POLICY_MESSAGE =
  "Selected audio file metadata violates the analysis resource policy.";

function fractionalBootstrap(projectId: string) {
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
      fileSizeBytes: 1.5
    }
  };
}

describe("analysis encoded-byte policy parity", () => {
  beforeEach(() => {
    delete tauriWindow.__TAURI_INTERNALS__;
    delete tauriWindow.__TAURI_INVOKE__;
  });

  it("rejects fractional local-file metadata before project state", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue(fractionalBootstrap("local-project"));

    await expect(selectLocalAudioSource()).resolves.toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: INVALID_RESOURCE_POLICY_MESSAGE
      }
    });
  });

  it("rejects fractional imported-file metadata before project state", async () => {
    tauriWindow.__TAURI_INVOKE__ = vi.fn().mockResolvedValue(fractionalBootstrap("youtube-project"));

    await expect(importYoutubeUrl("https://youtu.be/4ozX4yFUC34")).resolves.toEqual({
      ok: false,
      error: {
        code: "invalid_request",
        message: INVALID_RESOURCE_POLICY_MESSAGE
      }
    });
  });
});
