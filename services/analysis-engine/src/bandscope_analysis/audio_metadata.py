"""Bounded source-container metadata preflight for local audio decoders.

Security Notes:
- The selected audio bytes and container headers are untrusted.
- This module reads metadata from an already-open caller-owned handle only; it
  does not open paths, decode PCM, follow URLs, or allocate a waveform.
- Malformed headers, unsupported source rates/channels, and overlong sources
  fail closed with the payload-free canonical policy error.
- A successful probe rewinds the handle so the downstream decoder receives the
  same source from its beginning.
"""

from __future__ import annotations

from typing import BinaryIO

import soundfile  # type: ignore[import-untyped]  # soundfile has no py.typed marker.

from bandscope_analysis.audio_resource_policy import (
    DEFAULT_AUDIO_RESOURCE_POLICY,
    AudioResourcePolicy,
    AudioResourcePolicyError,
)


def preflight_audio_metadata(
    fileobj: BinaryIO,
    policy: AudioResourcePolicy = DEFAULT_AUDIO_RESOURCE_POLICY,
) -> None:
    """Validate source metadata without decoding PCM and rewind the handle."""
    try:
        fileobj.seek(0)
        info = soundfile.info(fileobj)
        fileobj.seek(0)
        policy.validate_source_metadata(
            frames=info.frames,
            sample_rate=info.samplerate,
            channels=info.channels,
        )
    except AudioResourcePolicyError:
        raise
    except Exception as error:
        raise AudioResourcePolicyError("malformed_header") from error
