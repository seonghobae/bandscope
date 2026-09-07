"""Regression tests for branch arcs hidden by the former statement-only gate."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from bandscope_analysis import api as analysis_api
from bandscope_analysis import cli
from bandscope_analysis.chords.analyzer import ChordAnalyzer
from bandscope_analysis.chords.chord_recognizer import ChordRecognizer
from bandscope_analysis.exports import chart
from bandscope_analysis.roles.activity import compute_handoffs
from bandscope_analysis.roles.extractor import RoleExtractor
from bandscope_analysis.sections.segmenter import (
    _checkerboard_novelty_reference,
    detect_boundaries,
)
from bandscope_analysis.temporal import hits
from bandscope_analysis.transcription import api as transcription_api


def test_local_audio_feature_builder_preserves_untyped_empty_stem_result() -> None:
    """Keep a separator result unchanged when no stem-role map can be inferred."""
    request = {
        "sourceKind": "local_audio",
        "localSource": {"sourcePath": "/tmp/song.wav"},
    }
    separation_result = {"stems": {}}

    with (
        patch.object(
            analysis_api,
            "_stem_work_arrays_path",
            return_value=Path("/tmp/bandscope-arrays.npz"),
        ),
        patch.object(
            analysis_api,
            "_run_stem_separation_with_timeout",
            return_value=separation_result,
        ),
        patch.object(analysis_api, "_normalize_stem_role_types", return_value=None),
    ):
        result = analysis_api._build_local_audio_features(request)  # type: ignore[arg-type]

    assert result == separation_result


def test_chord_analyzer_deduplicates_user_and_recognized_chords() -> None:
    """Exercise duplicate branches for both user-entered and DSP chord sources."""
    analyzer = ChordAnalyzer()
    user_roles = [
        {"harmony": {"chord": "Am", "functionLabel": "vi", "source": "user"}},
        {"harmony": {"chord": "Am", "functionLabel": "repeat", "source": "user"}},
    ]
    recognized = [
        {"start_time": 0.0, "end_time": 1.0, "chord": "C", "confidence": "high"},
        {"start_time": 1.0, "end_time": 2.0, "chord": "C", "confidence": "low"},
    ]

    assert [item["chord"] for item in analyzer._extract_user_chords(user_roles)] == ["Am"]
    assert [item["chord"] for item in analyzer._chords_for_section(recognized, None)] == ["C"]


def test_chord_analyzer_all_no_chord_recognition_falls_back_to_legacy_confidence() -> None:
    """Use legacy confidence when recognizer output exists but every frame is no-chord."""
    analyzer = ChordAnalyzer()
    chords = [{"chord": "G", "functionLabel": "I", "source": "model"}]
    recognized = [{"start_time": 0.0, "end_time": 1.0, "chord": "N", "confidence": "low"}]

    assert analyzer._compute_section_confidence(chords, recognized, []) == ("medium", "model")


def test_chord_segment_builder_handles_zero_frames_without_final_segment() -> None:
    """Return no segment when the frame decoder has no frames to materialize."""
    recognizer = ChordRecognizer()
    empty_observations = np.empty((len(recognizer.chord_labels), 0), dtype=np.float64)

    with (
        patch.object(recognizer, "_build_observation_probs", return_value=empty_observations),
        patch.object(recognizer, "_viterbi_decode", return_value=np.array([], dtype=np.int64)),
    ):
        result = recognizer._create_chord_segments(
            np.empty((12, 0), dtype=np.float64),
            empty_observations,
            np.empty(0, dtype=np.float64),
            22_050,
        )

    assert result == []


def test_cli_forwards_empty_local_source_path_to_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Let the owning orchestration API validate an empty local source path."""
    payload = {
        "jobId": "job-empty-source",
        "request": {
            "sourceKind": "local_audio",
            "localSource": {"sourcePath": "", "fileName": "song.wav"},
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    with patch.object(
        cli,
        "run_analysis_job",
        return_value={"jobId": "job-empty-source", "state": "failed"},
    ) as run_analysis_job:
        assert cli.main() == 0

    assert run_analysis_job.call_args.args[0] == "job-empty-source"
    assert run_analysis_job.call_args.args[1] == payload["request"]
    assert json.loads(stdout.getvalue())["jobId"] == "job-empty-source"


def test_chart_section_without_active_roles_and_duplicate_priority_footer() -> None:
    """Render role-free section lines and deduplicate repeated footer priorities."""
    section = {
        "label": "verse",
        "timeRange": {"start": 0, "end": 10},
        "roles": [],
        "partGraph": [],
    }
    assert chart._section_lines([section]) == ["[00:00-00:10] VERSE"]

    role = {"id": "bass", "name": "Bass", "rehearsalPriority": "Lock with kick"}
    footer = chart._footer_lines(
        {"exportSummary": {}},
        [{"roles": [role]}, {"roles": [dict(role)]}],
    )
    assert footer == ["Priorities:", "  - Bass: Lock with kick"]


def test_handoffs_ignore_roles_that_exist_only_in_the_next_section() -> None:
    """Keep current-section output bounded when a new role appears next section."""
    handoffs = compute_handoffs(
        {"lead-vocal": True},
        {"lead-vocal": False, "new-synth": True},
    )

    assert handoffs == {"lead-vocal": (["new-synth"], [])}


def test_role_feature_extraction_handles_absent_and_partial_stem_evidence() -> None:
    """Leave fields empty when stems omit vocals or yield incomplete/no-chord evidence."""
    extractor = RoleExtractor()
    stems = {
        "bass": np.zeros(16, dtype=np.float32),
        "other": np.zeros(16, dtype=np.float32),
    }

    with (
        patch(
            "bandscope_analysis.ranges.pitch_tracker.PitchTracker.track",
            return_value={"lowest_note": "E1", "highest_note": ""},
        ),
        patch(
            "bandscope_analysis.chords.chord_recognizer.ChordRecognizer.recognize",
            return_value=[{"chord": "N"}],
        ),
    ):
        vocal_range, vocal_chord, bass_range, bass_chord = extractor._extract_features(
            stems, 22_050
        )

    assert vocal_range == {"lowestNote": "", "highestNote": ""}
    assert bass_range == {"lowestNote": "", "highestNote": ""}
    assert vocal_chord == ""
    assert bass_chord == ""


def test_checkerboard_reference_preserves_zero_novelty_without_division() -> None:
    """Keep a flat full-size SSM finite instead of normalizing a zero peak."""
    flat_ssm = np.zeros((6, 6), dtype=np.float64)

    novelty = _checkerboard_novelty_reference(flat_ssm, kernel_size=4)

    np.testing.assert_array_equal(novelty, np.zeros(6, dtype=np.float64))
    assert np.isfinite(novelty).all()


def test_boundary_detection_skips_peak_without_matching_frame_time() -> None:
    """Ignore a novelty peak whose frame index has no corresponding timestamp."""
    novelty = np.array([0.0, 0.0, 1.0, 0.0, 0.0], dtype=np.float64)
    frame_times = np.array([0.0, 1.0], dtype=np.float64)

    assert detect_boundaries(novelty, frame_times, duration=20.0) == [0.0]


def test_shared_hits_continue_after_an_energetic_stem_has_no_onsets() -> None:
    """Skip an onset-free energetic stem while continuing to inspect later stems."""
    energetic = {
        "vocals": np.ones(8, dtype=np.float64),
        "bass": np.ones(8, dtype=np.float64),
    }

    with (
        patch.object(hits, "_energetic_stems", return_value=energetic),
        patch.object(
            hits.librosa.onset,
            "onset_detect",
            side_effect=[np.array([], dtype=np.float64), np.array([1.0], dtype=np.float64)],
        ) as onset_detect,
    ):
        result = hits.detect_shared_hits(energetic, 22_050)

    assert result == []
    assert onset_detect.call_count == 2


def test_contiguous_regions_finishes_cleanly_after_an_unvoiced_frame() -> None:
    """Do not append a second region when the final frame already closed the region."""
    mask = np.array([True, False], dtype=np.bool_)

    assert transcription_api._contiguous_regions(mask) == [(0, 0)]
