"""VAD-in-training pre-processing — trim non-speech from positive/negative
clips so the trainer sees what the runtime detector sees.

Runtime gates wake detection through Silero VAD (`settings.wake.vad_threshold`
in JarvYZ): the wake model is only ever called on VAD-positive segments.
Training, until this module landed, did NOT match — the model saw raw
piper output (possibly with leading/trailing silence) and learned to
fire on silence-shaped inputs too. Textbook train/test distribution
mismatch, suspected major contributor to the FP/hr blowup on hey_loomini.

When `vad_in_training: True` is set in train overrides:
  1. Load Silero VAD (torch.hub, lazy + cached)
  2. For each wav under <slug>/positive_train, positive_test,
     negative_train, negative_test: run VAD → trim from first speech
     start to last speech end → re-pad/crop to exactly 2.0 seconds
     (16 kHz, mono)
  3. Save in place (atomic via .tmp + replace)
  4. Write `.vad_processed` sidecar marker so we don't double-process
     on subsequent runs

Reversibility: VAD-processing is destructive (the raw piper output is
overwritten). To return to raw clips, the user wipes the slug's dataset
(wipe_stale=True on /train) and lets clip_gen regenerate. The
`.vad_processed` marker gets wiped with everything else.

Design choice notes:
  * In-place processing chosen over a side-dir (e.g. positive_train_vad/)
    to keep OWW's training config simple — no need to swap paths per
    A/B variant. Tradeoff: needs an explicit wipe to revert.
  * Silero defaults used (no threshold knob exposed yet). Phase 0d's
    A/B is on/off; per-clip threshold tuning is a follow-up if needed.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..settings import settings


# Lazy-loaded singletons. Silero VAD is ~2 MB ONNX (or torch.jit) that
# downloads from torch.hub on first call; cached under ~/.cache/torch/hub/.
_VAD_MODEL: Any = None
_VAD_UTILS: Any = None
_TARGET_SR = 16000
_TARGET_SECONDS = 2.0
_TARGET_SAMPLES = int(_TARGET_SR * _TARGET_SECONDS)


def _load_silero():
    """First call downloads ~2 MB from snakers4/silero-vad to torch hub
    cache. Subsequent calls are instant. Errors propagate — the caller
    decides whether to fail the whole training run or just skip VAD."""
    global _VAD_MODEL, _VAD_UTILS
    if _VAD_MODEL is not None:
        return _VAD_MODEL, _VAD_UTILS
    import torch
    _VAD_MODEL, _VAD_UTILS = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
    )
    return _VAD_MODEL, _VAD_UTILS


def _vad_marker(slug: str) -> Path:
    return settings.runs_dir / slug / ".vad_processed"


def is_processed(slug: str) -> bool:
    return _vad_marker(slug).exists()


def _apply_vad_to_wav(path: Path) -> str:
    """Process one WAV in place. Returns one of:
      - "ok"        clip had speech, trimmed + repadded to 2s
      - "no_speech" VAD found nothing; clip left unchanged
      - "skip"      clip already exactly 2s @ 16 kHz mono AND VAD found speech
                    spanning most of it (already in target shape)
    Raises on torchaudio / IO failures."""
    import torch
    import torchaudio
    import torchaudio.functional as F

    waveform, sr = torchaudio.load(str(path))
    # Downmix to mono
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    # Resample to 16 kHz if needed
    if sr != _TARGET_SR:
        waveform = F.resample(waveform, sr, _TARGET_SR)
        sr = _TARGET_SR

    model, utils = _load_silero()
    get_speech_timestamps = utils[0]
    mono = waveform.squeeze(0)
    speech_ts = get_speech_timestamps(mono, model, sampling_rate=_TARGET_SR)
    if not speech_ts:
        return "no_speech"

    start = speech_ts[0]["start"]
    end = speech_ts[-1]["end"]
    trimmed = mono[start:end]

    # Re-pad / crop to exactly TARGET_SAMPLES, centering the speech
    cur = trimmed.shape[0]
    if cur == _TARGET_SAMPLES:
        out = trimmed
    elif cur > _TARGET_SAMPLES:
        # Center-crop
        crop_start = (cur - _TARGET_SAMPLES) // 2
        out = trimmed[crop_start : crop_start + _TARGET_SAMPLES]
    else:
        pad_total = _TARGET_SAMPLES - cur
        pad_left = pad_total // 2
        pad_right = pad_total - pad_left
        out = torch.nn.functional.pad(trimmed, (pad_left, pad_right))

    out = out.unsqueeze(0)  # back to [1, samples] for torchaudio.save
    # Use a stem-suffixed tmp + explicit format — torchaudio infers from
    # extension by default, and `.wav.tmp` reads as ".tmp" → unsupported.
    tmp = path.with_name(path.stem + ".tmp.wav")
    torchaudio.save(str(tmp), out, _TARGET_SR, format="wav")
    tmp.replace(path)
    return "ok"


def apply_to_slug(slug: str, *, force: bool = False) -> dict:
    """Process every wav under the slug's positive_train, positive_test,
    negative_train, negative_test directories. Idempotent — the
    `.vad_processed` sidecar marker short-circuits subsequent calls
    unless `force=True`."""
    marker = _vad_marker(slug)
    if marker.exists() and not force:
        return {
            "skipped": "already VAD-processed",
            "marker_mtime": marker.stat().st_mtime,
        }

    slug_dir = settings.runs_dir / slug
    if not slug_dir.exists():
        return {"error": f"no slug dir: {slug_dir}"}

    buckets = ("positive_train", "positive_test", "negative_train", "negative_test")
    results: dict[str, dict[str, int]] = {
        b: {"ok": 0, "no_speech": 0, "failed": 0, "total": 0} for b in buckets
    }
    started = time.time()
    for bucket in buckets:
        d = slug_dir / bucket
        if not d.exists():
            continue
        for wav in d.glob("*.wav"):
            results[bucket]["total"] += 1
            try:
                verdict = _apply_vad_to_wav(wav)
                if verdict == "ok":
                    results[bucket]["ok"] += 1
                elif verdict == "no_speech":
                    results[bucket]["no_speech"] += 1
            except Exception:  # noqa: BLE001 — single-file failures shouldn't kill the batch
                results[bucket]["failed"] += 1

    elapsed = time.time() - started
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"vad_processed_at={time.time():.3f}\nelapsed_seconds={elapsed:.1f}\n",
        encoding="utf-8",
    )
    results["elapsed_seconds"] = elapsed  # type: ignore[assignment]
    return results
