"""Chord recognizer using librosa's chromagrams with Viterbi smoothing."""

import logging
from typing import TypedDict

import librosa
import numpy as np

from .._native import HAVE_RUST, _viterbi_decode_rust

logger = logging.getLogger(__name__)

# Number of chord states: 12 major + 12 minor + 1 no-chord (N)
_NUM_CHORD_STATES = 25
_NO_CHORD_STATE = 24


class TrackedChord(TypedDict):
    """Result of chord recognition for a time segment."""

    start_time: float
    end_time: float
    chord: str
    confidence: str


class ChordRecognizer:
    """Extracts chords from audio data using chromagram template matching and Viterbi smoothing.

    Security Notes:
    - Processes untrusted audio arrays from stem separation.
    - No file I/O, network access, or shell execution.
    - Bounded computation: frame count capped by input duration.
    - Safe failure: exceptions in DSP steps return empty results.
    """

    def __init__(self) -> None:
        """Initialize the chord recognizer."""
        # Standard major/minor triads templates for 12 pitch classes
        # C, C#, D, D#, E, F, F#, G, G#, A, A#, B
        self.templates = self._build_templates()
        self.chord_labels = self._build_labels()
        self._transition_matrix = self._build_transition_matrix()

    def _build_templates(self) -> np.ndarray:
        """Build chromagram templates for 24 major and minor chords."""
        templates = np.zeros((24, 12))
        for i in range(12):
            # Major triad (0, 4, 7)
            templates[i, i] = 1.0
            templates[i, (i + 4) % 12] = 1.0
            templates[i, (i + 7) % 12] = 1.0

            # Minor triad (0, 3, 7)
            templates[i + 12, i] = 1.0
            templates[i + 12, (i + 3) % 12] = 1.0
            templates[i + 12, (i + 7) % 12] = 1.0

        # Normalize templates
        norms = np.linalg.norm(templates, axis=1, keepdims=True)
        templates = np.where(norms > 0, templates / norms, templates)
        return templates

    def _build_labels(self) -> list[str]:
        """Build labels corresponding to the templates."""
        notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        labels = []
        for note in notes:
            labels.append(note)  # Major
        for note in notes:
            labels.append(f"{note}m")  # Minor
        labels.append("N")  # No-chord state
        return labels

    def _build_transition_matrix(self) -> np.ndarray:
        """Build a chord-to-chord transition probability matrix for Viterbi decoding.

        Encodes musical priors:
        - High self-transition probability (chords tend to persist).
        - Higher probability for musically related transitions (fifths, relative).
        - Low but uniform probability for distant transitions.
        - N (no-chord) transitions handled separately.
        """
        n = _NUM_CHORD_STATES
        # Start with a small uniform baseline
        trans = np.full((n, n), 0.01 / n)

        # Self-transition is high (chords persist)
        self_prob = 0.8
        for i in range(n):
            trans[i, i] = self_prob

        # Musically related transitions for chord states (0-23)
        related_prob = 0.03
        for i in range(24):
            root = i % 12
            # Fifth relationship (e.g. C -> G, Am -> Em)
            fifth = (root + 7) % 12
            fourth = (root + 5) % 12
            relative = (root + 3) % 12 if i < 12 else (root + 9) % 12

            # Same root major/minor interchange
            parallel = (i + 12) % 24
            trans[i, parallel] = related_prob

            # Fifth up (both major and minor targets)
            trans[i, fifth] = related_prob
            trans[i, fifth + 12] = related_prob * 0.5

            # Fourth up
            trans[i, fourth] = related_prob * 0.7
            trans[i, fourth + 12] = related_prob * 0.3

            # Relative major/minor
            if i < 12:
                trans[i, relative + 12] = related_prob
            else:
                trans[i, relative] = related_prob

        # N state transitions
        no_chord_self = 0.6
        trans[_NO_CHORD_STATE, _NO_CHORD_STATE] = no_chord_self
        enter_chord = (1.0 - no_chord_self) / 24
        for j in range(24):
            trans[_NO_CHORD_STATE, j] = enter_chord

        exit_to_n = 0.02
        for i in range(24):
            trans[i, _NO_CHORD_STATE] = exit_to_n

        # Normalize rows
        row_sums = trans.sum(axis=1, keepdims=True)
        trans = np.where(row_sums > 0, trans / row_sums, trans)
        return trans

    def _viterbi_decode(self, observation_probs: np.ndarray) -> np.ndarray:
        """Run Viterbi algorithm over frame observations to smooth chord sequence.

        Uses the Rust ``bandscope_numeric`` kernel when available (the default),
        falling back to the NumPy reference implementation otherwise. Both paths
        return identical decoded state sequences (see
        ``tests/test_numeric_parity.py``).

        Args:
            observation_probs: Shape (n_states, n_frames) observation likelihood.

        Returns:
            Array of best state indices per frame, shape (n_frames,).
        """
        if HAVE_RUST and _viterbi_decode_rust is not None:  # pragma: no cover - native path
            trans = np.ascontiguousarray(self._transition_matrix, dtype=np.float64)
            obs = np.ascontiguousarray(observation_probs, dtype=np.float64)
            return _viterbi_decode_rust(trans, obs).astype(np.intp)
        return self._viterbi_decode_reference(observation_probs)

    def _viterbi_decode_reference(self, observation_probs: np.ndarray) -> np.ndarray:
        """Pure-NumPy reference implementation of log-space Viterbi decoding.

        Retained as the parity oracle for the Rust port and as the runtime
        fallback when the compiled extension is unavailable. Do not "optimize"
        the math here: it defines the canonical decoded sequence.
        """
        n_states, n_frames = observation_probs.shape
        if n_frames == 0:
            return np.array([], dtype=np.intp)

        # Work in log-space to avoid underflow
        log_trans = np.log(self._transition_matrix + 1e-12)
        log_obs = np.log(observation_probs + 1e-12)

        # Uniform initial probability
        log_pi = np.full(n_states, np.log(1.0 / n_states))

        # Viterbi tables
        viterbi = np.zeros((n_states, n_frames))
        backpointer = np.zeros((n_states, n_frames), dtype=np.intp)

        # Initialization
        viterbi[:, 0] = log_pi + log_obs[:, 0]

        # Forward pass (Vectorized over states for ~7x speedup)
        for t in range(1, n_frames):
            trans_probs = viterbi[:, t - 1, np.newaxis] + log_trans
            backpointer[:, t] = np.argmax(trans_probs, axis=0)
            viterbi[:, t] = np.max(trans_probs, axis=0) + log_obs[:, t]

        # Backtrace
        states = np.zeros(n_frames, dtype=np.intp)
        states[-1] = int(np.argmax(viterbi[:, -1]))
        for t in range(n_frames - 2, -1, -1):
            states[t] = backpointer[states[t + 1], t + 1]

        return states

    def _compute_confidence(self, similarity: np.ndarray, best_state: int) -> str:
        """Compute confidence for a chord prediction based on entropy of similarity scores.

        Lower entropy (peaked distribution) => higher confidence.

        Args:
            similarity: Similarity scores for a single frame, shape (n_templates,).
            best_state: The selected chord state index.

        Returns:
            Confidence level string: 'low', 'medium', or 'high'.
        """
        if best_state == _NO_CHORD_STATE:
            return "low"

        # Normalize similarities to probability distribution
        sim_shifted = similarity - similarity.max()
        exp_sim = np.exp(sim_shifted * 3.0)  # temperature scaling
        probs = exp_sim / (exp_sim.sum() + 1e-12)

        # Compute entropy
        entropy = -np.sum(probs * np.log(probs + 1e-12))
        max_entropy = np.log(len(similarity))

        # Normalized entropy (0 = peaked, 1 = uniform)
        norm_entropy = entropy / max_entropy if max_entropy > 0 else 1.0

        if norm_entropy < 0.5:
            return "high"
        if norm_entropy < 0.75:
            return "medium"
        return "low"

    def _separate_harmonic(self, y: np.ndarray) -> np.ndarray:
        """Separate harmonic component from audio."""
        try:
            y_harmonic, _ = librosa.effects.hpss(y)
            return np.asarray(y_harmonic)
        except Exception:
            return y

    def _extract_chromagram(self, y_harmonic: np.ndarray, sr: int) -> np.ndarray | None:
        """Extract and smooth chromagram."""
        try:
            if len(y_harmonic) <= sr * 2:
                chromagram = librosa.feature.chroma_stft(
                    y=y_harmonic,
                    sr=sr,
                    n_fft=min(2048, len(y_harmonic)),
                    hop_length=512,
                )
            else:
                chromagram = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
        except Exception:
            return None

        if chromagram.size == 0:
            return None

        # Optional: apply temporal smoothing to chromagram to reduce noise
        chromagram = librosa.decompose.nn_filter(chromagram, aggregate=np.median, metric="cosine")
        return np.asarray(chromagram)

    def _calculate_rms(self, y: np.ndarray, chromagram_len: int) -> np.ndarray:
        """Calculate RMS energy to detect silence/noise."""
        try:
            rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
            # Match RMS length to chromagram length
            if len(rms) < chromagram_len:
                rms = np.pad(rms, (0, chromagram_len - len(rms)), mode="edge")
            else:
                rms = rms[:chromagram_len]
        except Exception:
            rms = np.ones(chromagram_len)
        return np.asarray(rms)

    def _match_templates(self, chromagram: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Match chromagram to templates and return similarities and best match indices."""
        # Compare chromagram frames to templates using dot product.
        # chromagram shape: (12, n_frames)
        # templates shape: (24, 12)
        # similarity shape: (24, n_frames)
        similarity = np.dot(self.templates, chromagram)
        return similarity, np.argmax(similarity, axis=0)

    def _build_observation_probs(
        self,
        chromagram: np.ndarray,
        similarity: np.ndarray,
        rms: np.ndarray,
    ) -> np.ndarray:
        """Build observation probability matrix for Viterbi including N state.

        Args:
            chromagram: Shape (12, n_frames).
            similarity: Shape (24, n_frames) template similarities.
            rms: Shape (n_frames,) RMS energy.

        Returns:
            Observation probabilities of shape (25, n_frames).
        """
        n_frames = chromagram.shape[1]
        obs_probs = np.zeros((_NUM_CHORD_STATES, n_frames))

        # Chord observation likelihoods from template similarity
        # Normalize similarity per frame to get valid probability-like values
        sim_max = similarity.max(axis=0, keepdims=True)
        sim_shifted = similarity - sim_max
        exp_sim = np.exp(sim_shifted * 2.0)
        sim_sum = exp_sim.sum(axis=0, keepdims=True) + 1e-12
        obs_probs[:24, :] = exp_sim / sim_sum

        # N (no-chord) observation probability based on noise indicators
        chroma_vars = np.var(chromagram, axis=0)
        for i in range(n_frames):
            rms_val = rms[i] if i < len(rms) else 0.0
            chroma_var = chroma_vars[i]
            max_sim = similarity[:, i].max() if similarity.shape[1] > i else 0.0

            # High N probability when signal is low/flat
            if max_sim < 0.3 or rms_val < 0.01 or chroma_var < 0.02:
                obs_probs[:24, i] *= 0.1
                obs_probs[_NO_CHORD_STATE, i] = 0.9
            else:
                obs_probs[_NO_CHORD_STATE, i] = 0.05

        # Normalize columns
        col_sums = obs_probs.sum(axis=0, keepdims=True) + 1e-12
        obs_probs = obs_probs / col_sums

        return np.asarray(obs_probs)

    def _create_chord_segments(
        self,
        chromagram: np.ndarray,
        similarity: np.ndarray,
        rms: np.ndarray,
        sr: int,
    ) -> list[TrackedChord]:
        """Convert frame-level chord predictions into time segments using Viterbi."""
        n_frames = chromagram.shape[1]

        # Build observation probabilities and run Viterbi for smooth decoding
        obs_probs = self._build_observation_probs(chromagram, similarity, rms)
        decoded_states = self._viterbi_decode(obs_probs)

        frames = librosa.frames_to_time(np.arange(n_frames + 1), sr=sr)
        chords: list[TrackedChord] = []
        current_chord = None
        current_confidence = "low"
        start_frame = 0

        for i in range(n_frames):
            state = int(decoded_states[i])
            chord_label = self.chord_labels[state]

            # Compute per-frame confidence from the similarity distribution
            frame_confidence = self._compute_confidence(similarity[:, i], state)

            if current_chord is None:
                current_chord = chord_label
                current_confidence = frame_confidence
                start_frame = i
            elif chord_label != current_chord:
                chords.append(
                    {
                        "start_time": float(frames[start_frame]),
                        "end_time": float(frames[i]),
                        "chord": current_chord,
                        "confidence": current_confidence,
                    }
                )
                current_chord = chord_label
                current_confidence = frame_confidence
                start_frame = i
            else:
                # Update confidence: use the minimum across the segment
                # (conservative estimate)
                if _confidence_rank(frame_confidence) < _confidence_rank(current_confidence):
                    current_confidence = frame_confidence

        # Add final segment
        if current_chord is not None:
            chords.append(
                {
                    "start_time": float(frames[start_frame]),
                    "end_time": float(frames[-1] if len(frames) > 0 else 0.0),
                    "chord": current_chord,
                    "confidence": current_confidence,
                }
            )

        return chords

    def recognize(self, y: np.ndarray, sr: int = 22050) -> list[TrackedChord]:
        """Recognize chords in an audio array using chromagrams with Viterbi smoothing.

        Args:
            y: Audio time series.
            sr: Sampling rate.

        Returns:
            List of TrackedChord dicts with start_time, end_time, chord, and confidence.
        """
        if y.size == 0:
            return []

        y_harmonic = self._separate_harmonic(y)
        chromagram = self._extract_chromagram(y_harmonic, sr)

        if chromagram is None:
            return []

        rms = self._calculate_rms(y, chromagram.shape[1])
        similarity, _best_matches = self._match_templates(chromagram)

        return self._create_chord_segments(chromagram, similarity, rms, sr)


def _confidence_rank(level: str) -> int:
    """Convert confidence level string to numeric rank for comparison."""
    return {"low": 0, "medium": 1, "high": 2}.get(level, 0)
