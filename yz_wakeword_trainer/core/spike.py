"""Live model test ("spike"). Loads a trained .onnx and runs it against
mic audio in real time, exposing the latest score for the UI.

Producer-side test: the model we just trained, against the satellite's
own audio device, with inference HERE in the satellite. No live-runtime
dependency on JarvYZ. Same code path works in JarvYZ-embedded mode (UI
hits JarvYZ's proxy routes which forward here) and standalone mode (UI
hits the satellite directly).

Single-concurrent: only one spike session at a time. Lives in-process —
audio capture is a sounddevice callback thread, inference happens in
the callback per 80ms window, scores get written to module-level state
the HTTP routes read directly.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sounddevice as sd

from ..settings import settings


_SAMPLE_RATE = 16_000
_WINDOW = 1280  # OWW expects 80ms @ 16 kHz windows


@dataclass
class _State:
    running: bool = False
    slug: str = ""
    started_at: float = 0.0
    latest_score: float = 0.0
    peak_score: float = 0.0
    history: list[list[float]] = field(default_factory=list)  # [[ts, score], ...]
    error: str = ""


_state = _State()
_lock = threading.Lock()
_stream: Any = None  # sd.InputStream
_stop_event = threading.Event()


def status() -> dict:
    """Snapshot of current spike state. Includes recent score history so
    the UI can render a tiny sparkline without a separate fetch."""
    with _lock:
        return {
            "running": _state.running,
            "slug": _state.slug,
            "started_at": _state.started_at,
            "latest_score": _state.latest_score,
            "peak_score": _state.peak_score,
            "error": _state.error,
            # Last ~5 seconds of scores (12.5 Hz × 5 = ~60 points). UI plots a
            # sparkline / level meter from this. Cheap to send each poll.
            "recent": list(_state.history[-60:]),
        }


def start(slug: str, device: str | int | None = None) -> dict:
    """Start a spike session. Loads the trained ONNX + opens an audio
    stream. Raises if one is already running or the slug has no ONNX."""
    global _stream
    from openwakeword.model import Model  # lazy — OWW is heavy

    with _lock:
        if _state.running:
            raise RuntimeError(f"spike already running for slug={_state.slug!r}")

    onnx = settings.runs_dir / f"{slug}.onnx"
    if not onnx.exists():
        raise RuntimeError(f"no .onnx for slug {slug!r} at {onnx}")

    model = Model(wakeword_models=[str(onnx)], inference_framework="onnx")
    target_keys = list(model.models.keys())

    # Reset state
    with _lock:
        _state.running = True
        _state.slug = slug
        _state.started_at = time.time()
        _state.latest_score = 0.0
        _state.peak_score = 0.0
        _state.history = []
        _state.error = ""
    _stop_event.clear()

    # Per-callback buffer + accumulator. Captured by closure.
    buf: list[np.ndarray] = []
    samples_buffered = 0

    def on_audio(indata, _frames, _t_info, _status):
        nonlocal samples_buffered
        if _stop_event.is_set():
            return
        # Convert to int16 mono — what OWW expects.
        x = indata[:, 0].astype(np.float32)
        x_int16 = np.clip(x * 32768.0, -32768, 32767).astype(np.int16)
        buf.append(x_int16)
        samples_buffered += len(x_int16)

        if samples_buffered < _WINDOW:
            return

        # Peel complete 80ms windows off the front of the buffer.
        audio = np.concatenate(buf)
        buf.clear()
        samples_buffered = 0
        while len(audio) >= _WINDOW:
            window = audio[:_WINDOW]
            audio = audio[_WINDOW:]
            try:
                preds = model.predict(window)
            except Exception as e:
                with _lock:
                    _state.error = f"inference failed: {e}"
                return
            score = max((preds.get(k, 0.0) for k in target_keys), default=0.0)
            now = time.time()
            with _lock:
                _state.latest_score = float(score)
                if score > _state.peak_score:
                    _state.peak_score = float(score)
                _state.history.append([now, float(score)])
                # Cap history at ~16 seconds @ 12.5 Hz
                if len(_state.history) > 200:
                    _state.history = _state.history[-200:]
        # Re-buffer the leftover tail
        if len(audio):
            buf.append(audio)
            samples_buffered = len(audio)

    try:
        _stream = sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=512,
            device=device,
            callback=on_audio,
        )
        _stream.start()
    except Exception as e:
        with _lock:
            _state.running = False
            _state.error = f"audio open failed: {e}"
        raise

    return status()


def stop() -> dict:
    """Stop the active spike session. Idempotent — returns current state."""
    global _stream
    if not _state.running:
        return status()
    _stop_event.set()
    if _stream is not None:
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
        _stream = None
    with _lock:
        _state.running = False
    return status()
