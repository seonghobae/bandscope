"""Device-boundary regressions for local Demucs separation."""

from __future__ import annotations

import sys
from types import ModuleType

import numpy as np
import pytest

from bandscope_analysis.separation.audio_separator import AudioSeparationConfig, AudioStemSeparator


class _FakeModel:
    """Expose the canonical Demucs source order used by production."""

    sources = ["drums", "bass", "other", "vocals"]


class _DeviceTensor:
    """Minimal tensor that refuses NumPy conversion until moved to CPU."""

    def __init__(self, array: np.ndarray, *, on_cpu: bool) -> None:
        self.array = np.asarray(array, dtype=np.float32)
        self.on_cpu = on_cpu

    def float(self) -> "_DeviceTensor":
        return _DeviceTensor(self.array.astype(np.float32), on_cpu=self.on_cpu)

    def mean(self, axis: int | None = None) -> float | "_DeviceTensor":
        value = self.array.mean(axis=axis)
        if axis is None:
            return float(value)
        return _DeviceTensor(np.asarray(value, dtype=np.float32), on_cpu=self.on_cpu)

    def std(self) -> float:
        return float(self.array.std())

    def cpu(self) -> "_DeviceTensor":
        return _DeviceTensor(self.array, on_cpu=True)

    def numpy(self) -> np.ndarray:
        if not self.on_cpu:
            raise RuntimeError("can't convert cuda tensor to numpy")
        return self.array

    def __getitem__(self, key: object) -> "_DeviceTensor":
        return _DeviceTensor(self.array[key], on_cpu=self.on_cpu)

    def __add__(self, value: float) -> "_DeviceTensor":
        return _DeviceTensor(self.array + value, on_cpu=self.on_cpu)

    def __sub__(self, value: float) -> "_DeviceTensor":
        return _DeviceTensor(self.array - value, on_cpu=self.on_cpu)

    def __mul__(self, value: float) -> "_DeviceTensor":
        return _DeviceTensor(self.array * value, on_cpu=self.on_cpu)

    def __truediv__(self, value: float) -> "_DeviceTensor":
        return _DeviceTensor(self.array / value, on_cpu=self.on_cpu)


class _NoGrad:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


def test_apply_model_moves_device_output_to_cpu_before_numpy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU-selected separation must cross the device boundary before NumPy conversion."""
    calls: dict[str, object] = {}
    fake_torch = ModuleType("torch")
    fake_torch.from_numpy = lambda array: _DeviceTensor(array, on_cpu=True)  # type: ignore[attr-defined]
    fake_torch.no_grad = _NoGrad  # type: ignore[attr-defined]

    def fake_apply_model(
        model: _FakeModel,
        batch: _DeviceTensor,
        *,
        device: str,
        split: bool,
        overlap: float,
        progress: bool,
    ) -> _DeviceTensor:
        calls.update(device=device, split=split, overlap=overlap, progress=progress)
        source_values = np.arange(len(model.sources), dtype=np.float32).reshape(-1, 1, 1)
        separated = np.broadcast_to(source_values, (len(model.sources), 2, 4)).copy()
        return _DeviceTensor(separated[None], on_cpu=False)

    demucs_module = ModuleType("demucs")
    apply_module = ModuleType("demucs.apply")
    apply_module.apply_model = fake_apply_model  # type: ignore[attr-defined]
    demucs_module.apply = apply_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "demucs", demucs_module)
    monkeypatch.setitem(sys.modules, "demucs.apply", apply_module)

    audio = np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)
    separator = AudioStemSeparator(AudioSeparationConfig(device="cuda", overlap=0.375))

    result = separator._apply_model(_FakeModel(), audio)

    assert calls == {"device": "cuda", "split": True, "overlap": 0.375, "progress": False}
    assert set(result) == set(_FakeModel.sources)
    assert all(stem.shape == (4,) for stem in result.values())
