"""Native clip generation for openWakeWord training.

Replaces openWakeWord's reliance on `piper-sample-generator` (which depends
on the old unbuildable-on-Windows `piper-phonemize`). Uses `piper-tts` 1.4+
directly — same .onnx voice format, but the new library publishes Windows
wheels and embeds espeak-ng. Result: full-Windows-native training.

Contract:
- OWW expects four directories under `<runs_dir>/<slug>/`:
    positive_train/  positive_test/  negative_train/  negative_test/
  each containing N WAVs. OWW counts files; if "~enough" exist, it skips
  its own generation step.
- We populate those dirs by synthesizing the target phrase (positives)
  and adversarial phrases (negatives) across multiple voices, with
  small per-clip param variation for diversity.

Resumable: filenames are zero-padded indices; we skip indices that already
exist on disk. Re-running picks up where it left off.

Performance: ~150-200ms/clip on CPU with `onnxruntime`, ~70ms with
`onnxruntime-gpu`. 50k clips → ~2.5h CPU or ~1h GPU. Acceptable as a
one-time per-slug cost; OWW then caches the *features* across re-trainings.
"""
from __future__ import annotations

import logging
import os
import random
import subprocess
import sys
import wave
from pathlib import Path
from typing import Iterable

from piper import PiperVoice, SynthesisConfig

from ..settings import settings

log = logging.getLogger(__name__)


# ───────────────────────── voice management ───────────────────────────────


def ensure_voices(voice_names: Iterable[str]) -> list[Path]:
    """Make sure each named voice is on disk (download missing ones via
    `python -m piper.download_voices`). Returns the resolved .onnx paths
    in the order given."""
    settings.voices_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name in voice_names:
        p = settings.voices_dir / f"{name}.onnx"
        if not p.exists():
            log.info("downloading piper voice %s → %s", name, settings.voices_dir)
            res = subprocess.run(
                [sys.executable, "-m", "piper.download_voices", name,
                 "--data-dir", str(settings.voices_dir)],
                capture_output=True, text=True, timeout=300,
            )
            if res.returncode != 0:
                raise RuntimeError(
                    f"piper.download_voices failed for {name}: {res.stderr[:500]}"
                )
        paths.append(p)
    return paths


def _load_voices(voice_paths: list[Path], use_cuda: bool = True) -> list[PiperVoice]:
    """Load each voice once. Defaults to CUDA now that we ship
    onnxruntime-gpu — drops per-clip synthesis time from ~150 ms (CPU)
    to ~50 ms (CUDA). Falls back to CPU automatically if the GPU
    provider can't initialize (missing cuDNN, etc.) — piper-tts handles
    that internally via onnxruntime's provider fallback."""
    voices: list[PiperVoice] = []
    for p in voice_paths:
        log.info("loading voice %s (cuda=%s)", p.name, use_cuda)
        voices.append(PiperVoice.load(str(p), use_cuda=use_cuda))
    return voices


# ───────────────────────── clip synthesis ─────────────────────────────────


_TARGET_SR = 16000  # OWW training assumes 16 kHz throughout


def _synth_one(voice: PiperVoice, text: str, out_path: Path, cfg: SynthesisConfig) -> None:
    """Write a single 16-kHz WAV atomically.

    Piper voices output at their native sample rate (medium voices = 22.05 kHz).
    OWW's training pipeline expects 16 kHz throughout and raises ValueError
    on any mismatch in `data.augment_clips`. We resample on the way out so
    the dataset is OWW-ready by construction.

    Atomic write: synth + (optional) resample go to <out_path>.tmp, then
    rename → <out_path>. A SIGKILL mid-write leaves a .tmp behind that the
    next clip_gen pass will overwrite — never a partially-written .wav
    that crashes torchaudio/soundfile during `augment_clips`.
    """
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        with wave.open(str(tmp_path), "wb") as wf:
            voice.synthesize_wav(text, wf, syn_config=cfg)

        # Read back and resample if needed. Piper's WAV header has the true rate.
        import numpy as np
        from scipy.io import wavfile
        from scipy.signal import resample_poly

        sr, samples = wavfile.read(str(tmp_path))
        if sr != _TARGET_SR:
            from math import gcd
            g = gcd(_TARGET_SR, sr)
            up, down = _TARGET_SR // g, sr // g
            resampled = resample_poly(samples.astype(np.float32), up, down)
            resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
            wavfile.write(str(tmp_path), _TARGET_SR, resampled)

        # Atomic rename — only published as <name>.wav after the full write
        # + resample completes. os.replace is atomic on both Windows + POSIX.
        os.replace(tmp_path, out_path)
    except BaseException:
        # Any failure (including KeyboardInterrupt / SIGKILL midway) — leave
        # NO partial file at the canonical name. Caller's next pass retries.
        if tmp_path.exists():
            try: tmp_path.unlink()
            except OSError: pass
        raise


def _phrase_at_index(phrases: list[str], i: int, voice_n: int) -> str:
    """Round-robin phrases. Caller's index covers both phrase + voice
    slots — `phrases[i % len(phrases)]` for the phrase, voice rotation
    handled separately by caller."""
    return phrases[i % len(phrases)]


def _rand_cfg(rng: random.Random) -> SynthesisConfig:
    """Per-clip variation: tempo + voicing noise. Bounds chosen to keep
    speech recognizable (not robotic at extremes)."""
    return SynthesisConfig(
        length_scale=rng.uniform(0.85, 1.20),
        noise_scale=rng.uniform(0.45, 0.75),
        noise_w_scale=rng.uniform(0.70, 0.90),
    )


def synthesize_bucket(
    target_dir: Path,
    phrases: list[str],
    voices: list[PiperVoice],
    count: int,
    seed: int = 0,
    *,
    progress_every: int = 100,
    progress_log_path: Path | None = None,
) -> int:
    """Synthesize `count` WAVs into target_dir. Resumable — skips indices
    that already exist. Returns count of NEW clips actually written.

    If `progress_log_path` is set, periodic progress lines are appended
    there too (in addition to Python's logger) so the training log file
    that the UI tails shows live activity — otherwise the satellite log
    goes dead silent for minutes while clip_gen grinds. The line format
    is `clip_gen: <bucket_name>: <i+1>/<count>` so it's both
    machine-greppable and human-skimmable in the log panel."""
    target_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    written = 0
    bucket_name = target_dir.name
    # Initial line — non-zero only when this bucket has prior progress.
    # Tells the user immediately where we're picking up from instead of
    # leaving them to guess during the silence before the first
    # progress_every milestone.
    existing = sum(1 for _ in target_dir.glob("*.wav"))
    if progress_log_path and existing < count:
        try:
            with progress_log_path.open("a", encoding="utf-8") as f:
                f.write(f"clip_gen: {bucket_name}: starting at {existing}/{count}\n")
        except OSError:
            pass
    for i in range(count):
        out = target_dir / f"{i:06d}.wav"
        if out.exists():
            # Keep RNG advancing so re-runs produce the same future choices
            _ = _rand_cfg(rng)
            continue
        phrase = phrases[i % len(phrases)]
        voice = voices[i % len(voices)]
        cfg = _rand_cfg(rng)
        try:
            _synth_one(voice, phrase, out, cfg)
            written += 1
        except Exception as e:  # noqa: BLE001
            log.warning("synth failed idx=%d phrase=%r voice=%s: %s",
                        i, phrase[:40], type(voice).__name__, e)
            # Continue — partial dataset is better than aborting
        if progress_every and written and written % progress_every == 0:
            log.info("  %s: %d/%d written", bucket_name, i + 1, count)
            if progress_log_path:
                try:
                    with progress_log_path.open("a", encoding="utf-8") as f:
                        f.write(f"clip_gen: {bucket_name}: {i + 1}/{count}\n")
                except OSError:
                    pass
    if progress_log_path and written:
        try:
            with progress_log_path.open("a", encoding="utf-8") as f:
                f.write(f"clip_gen: {bucket_name}: done ({written} new)\n")
        except OSError:
            pass
    return written


# ─────────────────────── slug-level orchestration ─────────────────────────


def _read_config(config_path: Path) -> dict:
    """Read the rendered training_config.yaml. Only the fields we need."""
    import yaml
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def _resolve_counts(cfg: dict) -> tuple[int, int, int, int]:
    """Return (pos_train, pos_test, neg_train, neg_test). Env-var overrides
    via settings.clip_count_* take precedence over the YAML's n_samples /
    n_samples_val — useful for fast smoke tests."""
    base_train = int(cfg.get("n_samples", 50000))
    base_test = int(cfg.get("n_samples_val", 5000))
    pos_train = settings.clip_count_positive_train or base_train
    pos_test = settings.clip_count_positive_test or base_test
    neg_train = settings.clip_count_negative_train or base_train
    neg_test = settings.clip_count_negative_test or base_test
    return pos_train, pos_test, neg_train, neg_test


def generate_clips_for_slug(slug: str, config_path: Path, use_cuda: bool = True) -> dict:
    """Top-level entry: read the rendered config, ensure voices, synthesize
    all four buckets if missing. Idempotent — re-runs are cheap once the
    dataset is complete (just file-exists checks).

    Returns a summary dict with counts written per bucket."""
    cfg = _read_config(config_path)
    target_phrases = cfg.get("target_phrase") or []
    if isinstance(target_phrases, str):
        target_phrases = [target_phrases]
    if not target_phrases:
        target_phrases = [slug.replace("_", " ")]

    negatives = cfg.get("custom_negative_phrases") or []
    if not negatives:
        # Minimum viable adversarial set — random short English so OWW
        # has SOMETHING in the negative bucket. User should populate
        # custom_negative_phrases for production-quality models.
        negatives = ["hello", "goodbye", "weather", "music",
                     "good morning", "what time is it", "set a timer"]

    pos_train, pos_test, neg_train, neg_test = _resolve_counts(cfg)

    voice_paths = ensure_voices(settings.piper_voices)
    voices = _load_voices(voice_paths, use_cuda=use_cuda)

    slug_dir = settings.runs_dir / slug
    log.info("generating clips for %s → %s", slug, slug_dir)
    log.info("  voices: %s", [p.name for p in voice_paths])
    log.info("  positives: %d train + %d test (phrases=%d)",
             pos_train, pos_test, len(target_phrases))
    log.info("  negatives: %d train + %d test (phrases=%d)",
             neg_train, neg_test, len(negatives))

    # Live progress lands in the training log file (settings.log_path) so
    # the UI's log tail shows movement during the long synthesis pass
    # instead of looking hung. Default cadence: every 500 wavs per bucket.
    plp = settings.log_path
    out = {}
    out["positive_train"] = synthesize_bucket(
        slug_dir / "positive_train", target_phrases, voices, pos_train,
        seed=1, progress_every=500, progress_log_path=plp)
    out["positive_test"] = synthesize_bucket(
        slug_dir / "positive_test", target_phrases, voices, pos_test,
        seed=2, progress_every=500, progress_log_path=plp)
    out["negative_train"] = synthesize_bucket(
        slug_dir / "negative_train", negatives, voices, neg_train,
        seed=3, progress_every=500, progress_log_path=plp)
    out["negative_test"] = synthesize_bucket(
        slug_dir / "negative_test", negatives, voices, neg_test,
        seed=4, progress_every=500, progress_log_path=plp)
    log.info("clip gen complete for %s: %s", slug, out)
    return out
