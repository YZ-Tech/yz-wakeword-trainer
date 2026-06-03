"""The training subprocess owner.

Replaces JarvYZ's old `_wsl + systemd-run` chain with a clean Python
subprocess that works cross-platform (POSIX uses start_new_session +
killpg; Windows uses CREATE_NEW_PROCESS_GROUP + taskkill /T /F).

State lives on disk via core.state so the satellite can restart and
re-attach to a running training. Log file is owned by us — we tee
the child's stdout/stderr into it; the log_cleaner runs on read.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator

from . import clip_gen, log_cleaner, metrics, state
from ..settings import settings


# ─────────────────────────── host info ────────────────────────────────────

def gpu_info() -> str | None:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    return None


def gpu_free_mb() -> int | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free_bytes, _total = torch.cuda.mem_get_info(0)
        return int(free_bytes / 1024 / 1024)
    except Exception:
        return None


def python_version() -> str:
    return platform.python_version()


# ──────────────────────── config rendering ────────────────────────────────

def _render_runtime_config(slug: str, overrides: dict[str, Any]) -> Path:
    """Render the per-slug training_config.yaml the trainer will consume.

    The template ships with relative paths (`runs/...`, `corpora/...`)
    anchored at the satellite's cwd. We do:

      1. Substitute every `{{SLUG}}` token → the slug.
      2. Rewrite target_phrase block from overrides/meta.
      3. REPLACE custom_negative_phrases block from overrides/meta —
         the full template block (all sub-groups + section comments +
         blank separators) gets removed so per-slug negatives don't
         end up mixed with template defaults.
      4. Filter background_paths to drop entries whose dir is missing,
         then append settings.extra_background_paths. Keep
         background_paths_duplication_rate in lockstep — same count,
         matched by index from the template, default rate 1 for extras.
      5. Optionally override n_samples / n_samples_val / steps.

    No path strings are constructed here. The template + the cwd at
    spawn time are the single source of truth for layout.
    """
    if not settings.config_template.exists():
        raise RuntimeError(f"config template missing: {settings.config_template}")
    txt = settings.config_template.read_text(encoding="utf-8")

    meta = state.load_model(slug) or {}
    phrases = overrides.get("phrases") or meta.get("phrases") or [meta.get("phrase", slug.replace("_", " "))]
    # Negatives precedence (most-specific wins):
    #   1. explicit overrides (programmatic caller)
    #   2. per-model meta.negatives (UI: Edit Negatives dialog → PUT /models/{slug}/negatives)
    #   3. global settings.global_negative_phrases (UI: PUT /negatives)
    #   4. template's default custom_negative_phrases block (file untouched)
    # Levels 1-3 REPLACE the template block. Level 4 means we don't touch it.
    negatives = (
        overrides.get("negatives")
        or meta.get("negatives")
        or list(settings.global_negative_phrases)
        or []
    )

    import re as _re
    # 1. slug substitution. Hits model_name, target_phrase fallback line,
    # false_positive_validation_data_path's runs/<slug>/... segment.
    txt = txt.replace("{{SLUG}}", slug)

    # 2. target_phrase block (`m` only, NOT `s` — avoid greedy eat-the-file)
    phrase_lines = "\n".join(f'  - "{p}"' for p in phrases if p)
    txt = _re.sub(
        r"(?m)^target_phrase:[ \t]*\n(?:[ \t]*-[ \t]*.+\n)+",
        f"target_phrase:\n{phrase_lines}\n",
        txt,
        count=1,
    )

    # 3. custom_negative_phrases — REPLACE the FULL block when meta
    # provides one. The template's block has multiple sub-groups
    # separated by blank lines + section-header comments
    # (`# English filler / common chat` etc.). An earlier version of
    # this regex used `(?:[ \t]*(?:-|#).*\n)+` which stopped at the
    # first blank line — so the per-slug block was effectively
    # APPENDED to the surviving second template sub-group, producing
    # ~15 duplicate negatives in every rendered config. The pattern
    # below matches from the header until the next top-level YAML key
    # (the first line at column 0 that isn't blank), which correctly
    # spans every sub-group + separator.
    if negatives:
        neg_lines = "\n".join(f'  - "{n}"' for n in negatives)
        txt = _re.sub(
            r"(?m)^custom_negative_phrases:.*\n(?:[ \t]+.*\n|[ \t]*\n)*",
            f"custom_negative_phrases:\n{neg_lines}\n\n",
            txt,
            count=1,
        )

    # 4. background_paths + duplication_rate filter, in lockstep.
    # OWW expects 1:1 alignment between the path list and the rate
    # list. The template ships them paired by index; we have to keep
    # them paired after dropping missing-dir entries and appending
    # settings.extra_background_paths. An earlier version of this
    # block updated only background_paths, leaving rates one short
    # whenever an extra (e.g. JWT_EXTRA_BACKGROUND_PATHS=CV-DE) was
    # added — silent count mismatch.
    bg_match = _re.search(
        r"(?m)^background_paths:[ \t]*\n((?:[ \t]*-[ \t]*.+\n)+)",
        txt,
    )
    rate_match = _re.search(
        r"(?m)^background_paths_duplication_rate:[ \t]*\n((?:[ \t]*-[ \t]*.+\n)+)",
        txt,
    )
    if bg_match:
        # Parse template paths in template order.
        template_paths = [
            line.strip().lstrip("-").strip()
            for line in bg_match.group(1).splitlines()
            if line.strip()
        ]
        # Parse template rates in the same order, tolerating inline
        # trailing comments like "  - 8   # loom_room — user-supplied".
        template_rates: list[int] = []
        if rate_match:
            for line in rate_match.group(1).splitlines():
                m = _re.match(r"\s*-\s*(\d+)", line)
                if m:
                    template_rates.append(int(m.group(1)))
        # Keep only paths whose dir exists, paired with their
        # template-indexed rate. Default rate 1 if the template's
        # rate list was shorter than its path list (defensive).
        kept_pairs: list[tuple[str, int]] = []
        for i, path in enumerate(template_paths):
            if (settings.wakeword_root / path).exists():
                rate = template_rates[i] if i < len(template_rates) else 1
                kept_pairs.append((path, rate))
        # Append extras with default rate 1, forward-slashed for YAML
        # cleanliness on Windows.
        for extra in settings.extra_background_paths:
            if extra.exists():
                kept_pairs.append((str(extra).replace(chr(92), "/"), 1))
        if not kept_pairs:
            # OWW requires at least one path — synthesize a placeholder.
            empty = settings.wakeword_root / "corpora" / "backgrounds" / "_empty"
            empty.mkdir(parents=True, exist_ok=True)
            kept_pairs = [("corpora/backgrounds/_empty", 1)]
        bg_block = (
            "background_paths:\n"
            + "\n".join(f"  - {p}" for p, _ in kept_pairs)
            + "\n"
        )
        rate_block = (
            "background_paths_duplication_rate:\n"
            + "\n".join(f"  - {r}" for _, r in kept_pairs)
            + "\n"
        )
        txt = txt.replace(bg_match.group(0), bg_block)
        if rate_match:
            # Re-search — earlier replacement shifts offsets and the
            # cached match object is no longer valid against `txt`.
            rate_match_new = _re.search(
                r"(?m)^background_paths_duplication_rate:[ \t]*\n((?:[ \t]*-[ \t]*.+\n)+)",
                txt,
            )
            if rate_match_new:
                txt = txt.replace(rate_match_new.group(0), rate_block)

    # 5. Optional scalar overrides (smoke tests).
    for k in ("n_samples", "n_samples_val", "steps"):
        if k in overrides:
            txt = _re.sub(rf"(?m)^{k}:.*$", f"{k}: {int(overrides[k])}", txt, count=1)

    settings.runtime_config.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime_config.write_text(txt, encoding="utf-8")
    return settings.runtime_config


_FP_VAL_TARGET_SECONDS = int(os.environ.get("JWT_FP_VAL_SECONDS", "1800"))  # 30 min default
_FP_VAL_MIN_FRAMES = 4096  # ~5 min at 80 ms/frame; below this we recompute


def _ensure_fp_val_features(slug: str) -> int:
    """Generate fp_val_features.npy for OWW's training validation step.

    Shape: (T, 96) float32 — a long continuous block of feature frames
    from audio that does NOT contain the wake phrase. OWW slides a
    16-frame window over this during training to compute the false-
    positive rate (per hour of audio). A diverse + reasonably long
    holdout makes the metric statistically meaningful instead of noisy.

    Sources, in priority order — uses whatever exists, blends evenly:
      1. LibriSpeech .flac           (English speech)
      2. fma_small .mp3              (music)
      3. settings.extra_background_paths/*.mp3|.wav|.flac  (e.g. CV-DE)
      4. loom_room .wav              (user's room ambience)

    Idempotent: returns the existing frame count when the .npy is already
    sized adequately (>= _FP_VAL_MIN_FRAMES); recomputes otherwise.

    Tuning: JWT_FP_VAL_SECONDS env var overrides target seconds.
    """
    fp_path = settings.runs_dir / slug / "fp_val_features.npy"
    if fp_path.exists():
        try:
            import numpy as _np
            arr = _np.load(str(fp_path), mmap_mode="r")
            if arr.ndim == 2 and arr.shape[0] >= _FP_VAL_MIN_FRAMES and arr.shape[1] == 96:
                return int(arr.shape[0])
        except Exception:
            pass  # malformed → recompute

    import random
    import numpy as np
    import torchaudio
    import torchaudio.functional as F
    import openwakeword.utils

    target_sr = 16000
    target_samples = target_sr * _FP_VAL_TARGET_SECONDS

    # Collect candidate audio files from each available source, capped
    # per-source so any single corpus can't dominate. Per-source caps
    # are deliberately generous; we sample randomly within each pool.
    bg = settings.corpora_dir / "backgrounds"
    sources: list[tuple[str, list[Path]]] = []
    for label, sub, glob, cap in (
        ("librispeech", bg / "LibriSpeech" / "train-clean-100", "*.flac", 500),
        ("fma_small",   bg / "fma_small",                       "*.mp3",  500),
        ("loom_room",   bg / "loom_room",                       "*.wav",  200),
    ):
        if sub.exists():
            files = list(sub.rglob(glob))[:cap]
            if files:
                sources.append((label, files))
    for extra in settings.extra_background_paths:
        if not extra.exists():
            continue
        files: list[Path] = []
        for pattern in ("*.mp3", "*.wav", "*.flac", "*.ogg", "*.opus"):
            files.extend(extra.rglob(pattern))
            if len(files) >= 500:
                break
        if files:
            sources.append((f"extra:{extra.name}", files[:500]))

    if not sources:
        raise RuntimeError(
            f"need at least one background corpus under {bg} or in JWT_EXTRA_BACKGROUND_PATHS"
        )

    # Round-robin one file from each source until we hit target. RNG seed
    # per slug so a re-run yields a stable selection (helps debug FP-rate
    # changes across training runs of the same slug).
    rng = random.Random(hash(slug) & 0xFFFFFFFF)
    for _, files in sources:
        rng.shuffle(files)
    iters = [iter(files) for _, files in sources]

    chunks: list[np.ndarray] = []
    have = 0
    while have < target_samples:
        progress = have
        for it in iters:
            if have >= target_samples:
                break
            p = next(it, None)
            if p is None:
                continue
            try:
                waveform, sr = torchaudio.load(str(p))
            except Exception:
                continue  # codec/corrupt file → skip
            if sr != target_sr:
                waveform = F.resample(waveform, orig_freq=sr, new_freq=target_sr)
            mono = waveform.mean(dim=0).cpu().numpy()
            wav = (mono * 32767).astype(np.int16) if waveform.dtype.is_floating_point else mono.astype(np.int16)
            chunks.append(wav)
            have += len(wav)
        if have == progress:
            break  # all iterators exhausted before reaching target
    audio = np.concatenate(chunks)[:target_samples] if chunks else np.zeros(target_samples, dtype=np.int16)

    afx = openwakeword.utils.AudioFeatures(device="cpu")
    features = afx.embed_clips(audio[np.newaxis, :], batch_size=1)
    if features.ndim == 3:
        features = features.reshape(-1, features.shape[-1])

    fp_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write — np.save direct would leave a partial .npy on
    # SIGKILL that crashes the next training load. .tmp → os.replace
    # is atomic on both POSIX and Windows.
    #
    # CRITICAL: pass a file-object, NOT a path string, to np.save.
    # If you pass a string that doesn't end in ".npy", numpy silently
    # APPENDS ".npy" to the filename — so np.save("...npy.tmp", arr)
    # writes "...npy.tmp.npy" and the subsequent os.replace fails with
    # FileNotFoundError. The file-object form bypasses that override.
    tmp_path = fp_path.with_suffix(fp_path.suffix + ".tmp")
    try:
        with open(tmp_path, "wb") as fobj:
            np.save(fobj, features.astype(np.float32))
        os.replace(tmp_path, fp_path)
    except BaseException:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except OSError: pass
        raise
    return int(features.shape[0])


def _quarantine_short_backgrounds() -> tuple[int, int]:
    """Sweep configured background dirs for audio files shorter than
    2.000s (or files soundfile can't open at all) and move them to
    `<wakeword_root>/_quarantine/short_backgrounds/...`.

    Why: torch_audiomentations' BackgroundNoise assumes every clip is
    exactly 32000 samples (2 s at 16 kHz) and crashes mid-augment_clips
    on anything shorter:
        RuntimeError: shape '[1, 1, 32000]' is invalid for input of size 31910
    Cost the user a successful run on 2026-05-27 — one stray 1.99 s
    LibriSpeech FLAC out of 36k backgrounds killed OWW after clip_gen
    had already completed. Defense: catch + quarantine BEFORE OWW spawns.

    Idempotent + incremental: a `last_sweep_ts` marker is kept; only
    files with mtime > marker are re-checked. Typical /train pays
    near-zero (no new files since the last sweep). First call ever
    pays a full scan (~18 s for 36k files).

    Returns (moved_count, scanned_count). Quarantined files keep their
    relative path under `<quarantine>/<root.name>/...` so manual undo
    is mechanical."""
    import shutil
    try:
        import soundfile as sf
    except ImportError:
        return 0, 0

    bg_root = settings.wakeword_root / "corpora" / "backgrounds"
    quarantine_root = settings.wakeword_root / "_quarantine" / "short_backgrounds"
    marker = settings.wakeword_root / "_quarantine" / "last_sweep_ts"

    last_ts = 0.0
    if marker.exists():
        try:
            last_ts = float(marker.read_text("utf-8").strip())
        except (OSError, ValueError):
            pass

    audio_exts = {".wav", ".flac", ".mp3", ".ogg", ".m4a"}
    scan_roots = [bg_root] + list(settings.extra_background_paths or [])
    moved = 0
    scanned = 0

    for root in scan_roots:
        if not root or not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in audio_exts:
                continue
            try:
                if p.stat().st_mtime <= last_ts:
                    continue
            except OSError:
                continue
            scanned += 1
            too_short = False
            try:
                info = sf.info(str(p))
                if info.frames / max(info.samplerate, 1) < 2.0:
                    too_short = True
            except Exception:  # noqa: BLE001
                too_short = True  # unopenable = quarantine too
            if too_short:
                try:
                    rel = p.relative_to(root)
                    dest = quarantine_root / root.name / rel
                except ValueError:
                    dest = quarantine_root / "_other" / p.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(p), str(dest))
                    moved += 1
                except OSError:
                    pass

    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        marker.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass
    return moved, scanned


def _cleanup_macos_metadata() -> int:
    """Delete macOS metadata files (`.DS_Store`, `._*` resource forks) from
    the corpora tree. Some upstream zips (notably MIT IR Survey) ship them.
    OWW's training does `os.scandir(rir_paths)` and treats every entry as a
    loadable audio file — soundfile then crashes with `Format not recognised`
    when it hits `.DS_Store`. Cheap to scan; runs once per /train call."""
    removed = 0
    if settings.corpora_dir.exists():
        for p in settings.corpora_dir.rglob(".DS_Store"):
            try: p.unlink(); removed += 1
            except OSError: pass
        for p in settings.corpora_dir.rglob("._*"):
            if p.is_file():
                try: p.unlink(); removed += 1
                except OSError: pass
    return removed


def _check_partial_features(slug: str) -> int:
    """OWW's 'features already exist, skip' check is a false positive on
    partial sets. If 1-4 of the expected .npy files exist, wipe them so
    the next run regenerates cleanly. Returns count of files removed."""
    slug_dir = settings.runs_dir / slug
    if not slug_dir.exists():
        return 0
    npys = list(slug_dir.glob("*.npy"))
    if 0 < len(npys) < 5:
        for p in npys:
            try: p.unlink()
            except OSError: pass
        return len(npys)
    return 0


# ──── corruption detection — validate-tail from newest mtime ──────────────
#
# The threat: a SIGKILL mid-write can leave a complete-looking but corrupt
# file on disk. Subsequent runs trust the on-disk file and crash inside
# OWW / numpy / onnxruntime at load. WAVs are safe by construction
# (clip_gen uses atomic .tmp → rename so the published .wav is always
# complete; orphans show up as .tmp files that we sweep). But .npy and
# .onnx are written by third-party code (openwakeword.train) that we
# can't make atomic, so we have to validate after the fact.
#
# Efficient validation: a kill happens at one moment, so only the file(s)
# being written at that moment can be corrupt. Sort by mtime descending
# and walk newest → older. First file that loads cleanly proves
# everything older is also clean. Stop there. For a typical slug we
# check 1-2 files instead of all 5.

def _is_wav_ok(p: Path) -> bool:
    import wave
    try:
        with wave.open(str(p), "rb") as wf:
            wf.getnframes()
        return True
    except Exception:
        return False


def _is_npy_ok(p: Path) -> bool:
    import numpy as _np
    try:
        # mmap_mode='r' parses the header without loading the data. Truncated
        # files raise ValueError on header parse; the .shape access ensures
        # the header is at least consistent.
        arr = _np.load(p, mmap_mode="r", allow_pickle=False)
        _ = arr.shape
        return True
    except Exception:
        return False


def _is_onnx_ok(p: Path) -> bool:
    try:
        import onnxruntime as _ort
        _ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
        return True
    except Exception:
        return False


def _validate_tail(files: list[Path], validator) -> list[Path]:
    """Sort by mtime DESC, validate from newest backwards, return list of
    corrupt files found. Stops at first file that validates — everything
    older is guaranteed complete because the kill was a single moment in
    time."""
    if not files:
        return []
    try:
        ordered = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    corrupt: list[Path] = []
    for f in ordered:
        if validator(f):
            return corrupt
        corrupt.append(f)
    return corrupt  # everything corrupt — extreme edge


def _sweep_tmp_orphans(slug: str) -> int:
    """Delete *.tmp orphans from interrupted atomic-write attempts. Cheap
    and always-safe — a .tmp file's contents are never trusted by any
    consumer; it's either renamed-into-place or it's garbage."""
    slug_dir = settings.runs_dir / slug
    if not slug_dir.exists():
        return 0
    removed = 0
    for sub in ("positive_train", "positive_test", "negative_train", "negative_test"):
        d = slug_dir / sub
        if not d.exists():
            continue
        for p in d.glob("*.tmp"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _find_corrupt_artifacts(slug: str) -> list[Path]:
    """Walk the .npy and .onnx artifacts mtime-DESC. Return paths that
    fail to load. WAVs are NOT checked — atomic .tmp→rename means the
    canonical .wav is always complete by construction.

    Read-only: never deletes anything. Called by preflight (so the UI
    sees the count) and by start() (which deletes them only when the
    user explicitly clicked Continue)."""
    slug_dir = settings.runs_dir / slug
    corrupt: list[Path] = []
    if slug_dir.exists():
        npys = list(slug_dir.glob("*.npy"))
        corrupt.extend(_validate_tail(npys, _is_npy_ok))
    onnx = settings.runs_dir / f"{slug}.onnx"
    if onnx.exists():
        if not _is_onnx_ok(onnx):
            corrupt.append(onnx)
    return corrupt


def _delete_corrupt_artifacts(slug: str) -> int:
    """Recovery deletion — removes files that failed validation in
    _find_corrupt_artifacts. NOT the same as `_invalidate_stale_negative_data`:
    this only touches files that are demonstrably unloadable (they fail
    `_is_npy_ok` / `_is_onnx_ok`), so deletion is a no-op in terms of
    data loss — the file was already garbage. Not gated by
    `settings.wipe_enabled` because there's nothing here to protect."""
    removed = 0
    for p in _find_corrupt_artifacts(slug):
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# ─────────────────────────── lifecycle ────────────────────────────────────

def _write_banner(log_path: Path, slug: str) -> None:
    """Banner at the top of every fresh log file. Always-present marker so
    even an instant CUDA-OOM-kill leaves SOMETHING readable."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    free = gpu_free_mb()
    gpu = gpu_info() or "no CUDA"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    banner = (
        f"=== JarvYZ wakeword trainer launching at {ts} ===\n"
        f"model: {slug}\n"
        f"gpu: {gpu}\n"
        f"GPU free: {free} MB\n"
        f"python: {python_version()}\n"
        f"---\n"
    )
    log_path.write_text(banner, encoding="utf-8")


def _negatives_hash(slug: str) -> str:
    """Stable hash of the slug's current negatives list. Matches what the
    reaper writes into training_history rows so we can compare 'meta now'
    vs 'last training' and wipe stale data on mismatch."""
    import hashlib
    meta = state.load_model(slug) or {}
    blob = "\n".join(meta.get("negatives") or []).encode("utf-8")
    return "sha1:" + hashlib.sha1(blob).hexdigest()[:8]


def _last_trained_negatives_hash(slug: str) -> str | None:
    """The negatives_hash from the most recent training_history row, or
    None if the slug has never trained under the rich-history machinery."""
    meta = state.load_model(slug) or {}
    history = meta.get("training_history") or []
    for row in reversed(history):
        h = row.get("negatives_hash")
        if h:
            return h
    return None


# ─── on-disk dataset hash sidecar ─────────────────────────────────────────
#
# The training_history hash is a stale proxy for "what's on disk" — it
# only updates on SUCCESSFUL training, so failed-retry loops leave it
# stuck on an older hash even after clip_gen has regenerated under the
# new list. The sidecar fixes this: a tiny file written by trainer.start()
# AFTER clip_gen completes, recording exactly which negatives_hash the
# on-disk wavs were synthesized under.
#
# preflight reads the sidecar as the authority; the training_history
# check is now a fallback for legacy slugs whose sidecar has never been
# written.
#
# Safety rule: the sidecar is only updated when we KNOW the dataset is
# homogeneous under a single hash — either a fresh start (no existing
# wavs) or a resume where the prior sidecar already matched the current
# hash. A Continue across hash_drift produces a mixed dataset and does
# NOT update the sidecar; the drift state is preserved for next preflight.

def _dataset_hash_file(slug: str) -> Path:
    return settings.runs_dir / slug / ".clip_gen_negatives_hash"


def _on_disk_negatives_hash(slug: str) -> str | None:
    """Read the sidecar. Returns the hash recorded by the last clip_gen
    completion, or None if the sidecar has never been written for this
    slug."""
    p = _dataset_hash_file(slug)
    if not p.exists():
        return None
    try:
        h = p.read_text(encoding="utf-8").strip()
        return h or None
    except OSError:
        return None


def _write_dataset_hash(slug: str, h: str) -> None:
    """Atomic write of the sidecar. .tmp → os.replace so a kill mid-write
    leaves either the new value or the prior value, never garbage."""
    p = _dataset_hash_file(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(h, encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass
        raise


def _delete_dataset_hash(slug: str) -> None:
    """Remove the sidecar. Called as part of an explicit wipe so the next
    clip_gen writes a fresh value."""
    p = _dataset_hash_file(slug)
    if p.exists():
        try: p.unlink()
        except OSError: pass


def _count_existing_wavs(slug: str) -> int:
    """Total wavs across all 4 buckets — used to decide whether a clip_gen
    run starts from a fresh state."""
    slug_dir = settings.runs_dir / slug
    if not slug_dir.exists():
        return 0
    total = 0
    for sub in ("positive_train", "positive_test", "negative_train", "negative_test"):
        d = slug_dir / sub
        if d.exists():
            total += sum(1 for _ in d.glob("*.wav"))
    return total


def _invalidate_stale_negative_data(slug: str) -> int:
    """When negatives changed since the last training run, the cached
    negative clips + features + onnx are stale relative to the new list.
    Wipe them so the next clip_gen + augment_clips + training regenerates
    against the new negatives.

    Returns the count of files removed (for logging). Positive clips
    aren't touched — the wake phrase didn't change, so they're still
    valid.

    fp_val_features.npy is also nuked because the FP-rate metric should
    reflect the new model's behavior on fresh held-out audio. Cheap to
    recompute (90s of LibriSpeech).

    HARD GATE: `settings.wipe_enabled` must be True. Default is False.
    Without it, this function refuses to delete anything regardless of
    how it was called — even with `wipe_stale=True` from the API. The
    user has been burned by silent re-wipes during failed retry loops,
    so deletion requires an explicit out-of-band opt-in (env var
    JWT_WIPE_ENABLED=1 or PATCH /settings)."""
    if not settings.wipe_enabled:
        try:
            settings.log_path.parent.mkdir(parents=True, exist_ok=True)
            with settings.log_path.open("a", encoding="utf-8") as f:
                f.write(
                    "(WIPE BLOCKED: settings.wipe_enabled is False — refusing "
                    "to delete files for slug=%s. Set JWT_WIPE_ENABLED=1 in "
                    "the environment + restart the satellite to allow.)\n"
                    % slug
                )
        except OSError:
            pass
        return 0
    slug_dir = settings.runs_dir / slug
    if not slug_dir.exists():
        return 0
    removed = 0
    targets: list[Path] = []
    # Negative WAVs (both train + test buckets)
    for sub in ("negative_train", "negative_test"):
        d = slug_dir / sub
        if d.exists():
            targets.extend(d.glob("*.wav"))
    # All .npy features — they bake-in the negatives via augmentation
    targets.extend(slug_dir.glob("*.npy"))
    # Old onnx (would otherwise stick around even though it's now stale)
    onnx = settings.runs_dir / f"{slug}.onnx"
    if onnx.exists():
        targets.append(onnx)
    for p in targets:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    # Also drop the dataset-hash sidecar — the next clip_gen will rewrite
    # it under the new hash once the regen completes.
    _delete_dataset_hash(slug)
    # And the VAD-processed marker — if positives were VAD-trimmed in a
    # prior run, they're untouched here (wipe doesn't clear positives),
    # but the marker controls whether future train passes re-VAD them.
    # Clearing it forces a fresh VAD pass on next vad_in_training=True
    # run, which is the right behavior whenever the dataset changes.
    vad_marker = slug_dir / ".vad_processed"
    if vad_marker.exists():
        try: vad_marker.unlink()
        except OSError: pass
    return removed


def _parse_training_metrics_log(metrics_log: Path) -> dict | None:
    """Parse OWW's `training_metrics.log` for its three Final Model lines.
    Returns {'accuracy', 'recall', 'fp_per_hour'} or None if any of the
    three are missing / unparseable. OWW writes these AFTER training
    completes + before ONNX export, so their presence indicates a
    successful run."""
    if not metrics_log.exists():
        return None
    try:
        txt = metrics_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    import re as _re
    acc = _re.search(r"Final Model Accuracy:\s*([\d.]+)", txt)
    rec = _re.search(r"Final Model Recall:\s*([\d.]+)", txt)
    fph = _re.search(r"Final Model False Positives per Hour:\s*([\d.]+)", txt)
    if not (acc and rec and fph):
        return None
    return {
        "accuracy": float(acc.group(1)),
        "recall": float(rec.group(1)),
        "fp_per_hour": float(fph.group(1)),
    }


def recover_metrics_for_slug(slug: str) -> bool:
    """If `<runs>/<slug>.onnx` exists and is newer than this slug's
    last `training_history` row, parse metrics from OWW's
    `training_metrics.log` and append a synthesized history row marked
    `recovered=True`. Returns True on recovery, False otherwise.

    Idempotent: re-running is safe (the ts comparison gates the write).
    Defensive: handles the failure mode where the satellite died between
    OWW's ONNX export and the reaper writing the rich history row, OR
    when training succeeded but the reaper never ran (JarvYZ-side
    crash, kill -9, etc.)."""
    onnx = settings.runs_dir / f"{slug}.onnx"
    if not onnx.exists():
        return False
    try:
        onnx_mtime = onnx.stat().st_mtime
        onnx_size = onnx.stat().st_size
    except OSError:
        return False

    # Don't race the reaper. If a job is currently active (preparing /
    # running / failed) the reaper will write its rich, canonical row
    # shortly — let it. Recovery is only for the truly orphaned case
    # (no job state on disk, but ONNX exists and history is missing).
    if state.load_job() is not None:
        return False

    meta = state.load_model(slug) or {}
    history = meta.get("training_history") or []
    # Skip if ANY existing row is close in time to this ONNX (within 5
    # minutes). This catches both (a) the reaper-already-wrote case AND
    # (b) a prior recovery from a previous boot, preventing duplicate
    # rows for the same training run.
    for row in history:
        try:
            if abs(float(row.get("ts", 0.0)) - onnx_mtime) < 300.0:
                return False
        except (TypeError, ValueError):
            continue

    metrics_log = settings.runs_dir / slug / "training_metrics.log"
    metrics = _parse_training_metrics_log(metrics_log)
    if not metrics:
        return False

    sidecar = _on_disk_negatives_hash(slug)
    cur_hash = _negatives_hash(slug)
    row = {
        "ts": onnx_mtime,
        "config": None,  # unrecoverable from log alone; left None on purpose
        "negatives_count": len(meta.get("negatives") or []),
        "negatives_hash": sidecar or cur_hash,
        "elapsed_seconds": None,  # OWW doesn't print total elapsed
        "metrics": metrics,
        "onnx_size": onnx_size,
        "satellite_version": __version__,
        "recovered": True,
    }
    history.append(row)
    meta["training_history"] = history
    meta["metrics"] = metrics  # top-level mirror for back-compat
    meta["slug"] = slug  # safety: save_model requires slug
    state.save_model(meta)
    return True


def recover_metrics_all() -> list[str]:
    """Sweep every known slug for orphan ONNX files (newer than their
    history). Called on satellite startup so a crash between ONNX-export
    and reaper doesn't leave the UI showing stale metrics forever.
    Returns the list of slugs whose metrics were recovered."""
    recovered: list[str] = []
    for meta in state.list_models():
        slug = meta.get("slug")
        if not slug:
            continue
        try:
            if recover_metrics_for_slug(slug):
                recovered.append(slug)
        except Exception:  # noqa: BLE001
            pass
    return recovered


def preflight(slug: str) -> dict:
    """Pre-train probe. Looks at on-disk state for the slug and returns
    enough info for the UI to decide: start directly (clean/fresh) or
    surface a Continue / Clear-and-start-over choice (partial /
    full_no_onnx / hash_drift).

    States:
      - fresh        : never trained, no clips. Smart Start fires direct.
      - clean        : full dataset + onnx, hash unchanged. Smart Start.
      - partial      : some clips exist but counts < target. Resume fills
                       gaps; clear regens everything.
      - full_no_onnx : clips + features done but no onnx (training crashed
                       or was stopped post-clip_gen). Resume retries the
                       training step only.
      - hash_drift   : negatives changed since last successful training.
                       Continue trains on stale clips; clear regens.
    """
    cur_hash = _negatives_hash(slug)
    # Sidecar (`.clip_gen_negatives_hash`) is the authority for "what hash
    # the on-disk wavs were generated under." Falls back to the legacy
    # training_history check ONLY when no sidecar exists (slugs that
    # never went through the post-fix clip_gen pipeline).
    sidecar_hash = _on_disk_negatives_hash(slug)
    if sidecar_hash is not None:
        last_hash = sidecar_hash
    else:
        last_hash = _last_trained_negatives_hash(slug)
    would_wipe = bool(last_hash is not None and cur_hash != last_hash)

    slug_dir = settings.runs_dir / slug

    def _count_wavs(d: Path) -> int:
        if not d.exists():
            return 0
        return sum(1 for _ in d.glob("*.wav"))

    pos_train = _count_wavs(slug_dir / "positive_train")
    pos_test = _count_wavs(slug_dir / "positive_test")
    neg_train = _count_wavs(slug_dir / "negative_train")
    neg_test = _count_wavs(slug_dir / "negative_test")
    npy_features = sum(1 for _ in slug_dir.glob("*.npy")) if slug_dir.exists() else 0

    onnx = settings.runs_dir / f"{slug}.onnx"
    onnx_size = 0
    if onnx.exists():
        try:
            onnx_size = onnx.stat().st_size
        except OSError:
            pass

    # Target counts: env-var overrides win (smoke-test mode), else 50k/5k
    # defaults matching clip_gen._resolve_counts. The slug's runtime YAML
    # may override these too, but it only exists post-/train so we use
    # the same defaults the YAML would.
    target_train = settings.clip_count_positive_train or 50000
    target_test = settings.clip_count_positive_test or 5000

    has_any_clips = (pos_train + pos_test + neg_train + neg_test) > 0
    all_clips_full = (
        pos_train >= target_train and pos_test >= target_test and
        neg_train >= target_train and neg_test >= target_test
    )

    if would_wipe:
        state = "hash_drift"
    elif not has_any_clips and last_hash is None:
        state = "fresh"
    elif not has_any_clips:
        # No clips on disk but slug HAS trained before — onnx may still
        # exist from a prior run; treat as fresh-start opportunity.
        state = "fresh"
    elif not all_clips_full:
        state = "partial"
    elif onnx_size == 0:
        state = "full_no_onnx"
    else:
        state = "clean"

    # Corruption check — validate-tail from newest mtime. Cheap (1-2
    # file loads typical) and safe (read-only here; deletion happens
    # only in start() after the user clicks Continue).
    corrupt_files = [str(p.name) for p in _find_corrupt_artifacts(slug)]

    return {
        "slug": slug,
        "state": state,
        "would_wipe": would_wipe,
        "cur_hash": cur_hash,
        "last_hash": last_hash,
        "pos_train_clips": pos_train,
        "pos_test_clips": pos_test,
        "neg_train_clips": neg_train,
        "neg_test_clips": neg_test,
        "target_train": target_train,
        "target_test": target_test,
        "npy_features": npy_features,
        "onnx_size": onnx_size,
        "corrupt_files": corrupt_files,
        # Read-only view of the kill-switch so the UI can show whether
        # "Clear & start from new" is even available. Toggled only via
        # JWT_WIPE_ENABLED env var + satellite restart.
        "wipe_enabled": bool(settings.wipe_enabled),
    }


def dispatch(slug: str, overrides: dict[str, Any] | None = None,
             *, background: Any = None) -> str:
    """Quick-return entry point used by the HTTP handler.

    Writes a `state="preparing"` session marker SYNCHRONOUSLY so
    `/jobs/current` shows the new job within milliseconds, then schedules
    the long-running `start()` work (clip_gen synthesis, feature build,
    subprocess spawn) on a FastAPI BackgroundTask.

    Why split: clip_gen for a fresh slug can take MINUTES (~110k WAVs via
    piper-tts). The HTTP request can't hold open that long without the
    client's httpx timeout firing. By writing the session early + doing
    the work in background, the UI sees "preparing → running → done"
    transitions instead of an opaque 500.
    """
    overrides = overrides or {}
    # Synchronous preparing marker — visible to /jobs/current immediately.
    state.save_job({
        "job_id": state.CURRENT_JOB_ID,
        "slug": slug,
        "pid": 0,
        "started_at": time.time(),
        "log_path": str(settings.log_path),
        "state": "preparing",
        "cmd": "queued — clip_gen + fp_val + spawn pending",
    })
    if background is not None:
        background.add_task(_start_background, slug, overrides)
    else:
        # Direct invocation (CLI tools, tests). Block until spawn.
        _start_background(slug, overrides)
    return state.CURRENT_JOB_ID


def _start_background(slug: str, overrides: dict[str, Any]) -> None:
    """BackgroundTask wrapper around `start()` that converts crashes
    during clip_gen / spawn into a `state="failed"` session marker the
    UI can surface, instead of leaving a dangling "preparing" record."""
    try:
        start(slug, overrides=overrides)
    except Exception as e:  # noqa: BLE001
        import traceback
        sess = state.load_job() or {"slug": slug}
        sess.update({
            "state": "failed",
            "cmd": f"dispatch failed: {type(e).__name__}: {e}",
        })
        state.save_job(sess)
        # Mirror to the train.log so users debugging via the UI's log
        # panel see the cause.
        try:
            with settings.log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n*** dispatch failed ***\n{traceback.format_exc()}\n")
        except OSError:
            pass


def start(slug: str, overrides: dict[str, Any] | None = None) -> str:
    """Spawn the OWW training subprocess. Returns job_id.

    Single-concurrent: server.py guards against double-start via state.

    Smart cache invalidation: if the slug's negatives list changed since
    the last training run (compared by sha1 of the joined phrase list),
    wipe the stale negative clips + features + onnx before clip_gen so
    the new negatives actually take effect. No-op for slugs never trained
    or whose negatives match the last run.
    """
    overrides = overrides or {}

    # 0. Stale-data check. ONLY wipes when caller explicitly opted in via
    # `overrides["wipe_stale"] is True` — never automatically. Compares
    # the meta-list hash against the sidecar (`.clip_gen_negatives_hash`),
    # which is the authority for "what hash the on-disk data was
    # generated against" — same source preflight uses. training_history
    # is wrong here: it only updates on SUCCESSFUL training, so failed-
    # retry loops would warn forever even after the data was regenerated.
    cur_hash = _negatives_hash(slug)
    last_hash = _on_disk_negatives_hash(slug)
    wiped = 0
    if last_hash is not None and cur_hash != last_hash:
        if overrides.get("wipe_stale") is True:
            wiped = _invalidate_stale_negative_data(slug)
        else:
            # WARN ONLY — never wipe without consent. clip_gen will resume
            # from whatever's on disk; that data was synthesized against
            # the prior negatives_hash but the user opted not to regenerate.
            try:
                settings.log_path.parent.mkdir(parents=True, exist_ok=True)
                with settings.log_path.open("a", encoding="utf-8") as f:
                    f.write(
                        f"(WARN: negatives_hash changed since on-disk data "
                        f"was generated: {last_hash} -> {cur_hash}. On-disk "
                        f"data may not match current meta. Wipe skipped — "
                        f"pass wipe_stale=true to /train to regenerate.)\n"
                    )
            except OSError:
                pass

    # 1. Render the per-slug config
    config_path = _render_runtime_config(slug, overrides)

    # 1b. Defensive cleanup — drop macOS metadata files that may have come
    # along with corpora downloads (notably MIT IR Survey ships `.DS_Store`
    # inside its zip). OWW's `os.scandir(rir_paths)` doesn't filter, then
    # soundfile crashes loading the metadata file.
    _cleanup_macos_metadata()

    # 1c. Defensive sweep — quarantine any background audio file shorter
    # than 2.000s (or that soundfile can't open). torch_audiomentations
    # crashes on <32000-sample backgrounds. Incremental via mtime marker:
    # first call ever pays a full scan, every subsequent /train is a
    # near-no-op unless the user added new background recordings.
    short_moved, short_scanned = _quarantine_short_backgrounds()

    # 2. Clean partial features (false-positive guard)
    cleaned = _check_partial_features(slug)

    # 2b. Sweep `.tmp` orphans from interrupted atomic WAV writes + drop
    # any complete-looking-but-corrupt .npy/.onnx (validate-tail from
    # newest mtime — only the tail can be bad, older files are
    # guaranteed complete because the kill was a single moment in time).
    tmp_swept = _sweep_tmp_orphans(slug)
    corrupt_removed = _delete_corrupt_artifacts(slug)

    # 3. Fresh log file w/ banner (truncate prior content)
    log_path = settings.log_path
    _write_banner(log_path, slug)
    if cleaned:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"(cleaned {cleaned} partial features)\n")
    if tmp_swept:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"(swept {tmp_swept} .tmp orphans from interrupted writes)\n")
    if short_moved:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(
                f"(quarantined {short_moved} background audio files < 2.000s — "
                f"would have crashed torch_audiomentations.augment_clips)\n"
            )
    elif short_scanned:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"(scanned {short_scanned} new background files — all OK)\n")
    if corrupt_removed:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(
                f"(removed {corrupt_removed} corrupt artifacts that failed "
                f"validation — clip_gen / features will regenerate them)\n"
            )
    if wiped:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(
                f"(negatives changed since last run: {last_hash} -> {cur_hash};"
                f" wiped {wiped} stale files — clip_gen will regenerate)\n"
            )

    # 4. Generate clips natively (piper-tts) if any bucket is missing.
    # OWW's own piper-sample-generator path doesn't exist on Windows
    # (`piper-phonemize` blocker). We populate the four bucket dirs
    # directly; OWW then sees "~enough clips exist" and skips its gen
    # step. Idempotent — no-op once dataset is complete. Errors logged
    # but don't block the spawn (OWW might still proceed with cached
    # features only).
    # Capture pre-clip_gen state for the sidecar decision. If the dataset
    # was homogeneous under cur_hash entering this call (either empty
    # buckets after a wipe, or sidecar already matches cur_hash), then
    # the dataset stays homogeneous after clip_gen completes (clip_gen
    # only ADDS files under cur_hash, never replaces). If the prior
    # sidecar exists and DOESN'T match cur_hash, we're in a
    # Continue-across-hash_drift situation: clip_gen will fill new
    # (cur_hash) wavs alongside the surviving (old_hash) wavs, producing
    # a mixed dataset. In that case we leave the sidecar at its old
    # value (or missing) so the drift state is preserved for next
    # preflight — the user made the Continue choice consciously, we
    # don't paper over it.
    _pre_sidecar = _on_disk_negatives_hash(slug)
    _pre_wav_count = _count_existing_wavs(slug)
    _sidecar_safe = (
        _pre_wav_count == 0
        or (_pre_sidecar is not None and _pre_sidecar == cur_hash)
    )

    try:
        clip_summary = clip_gen.generate_clips_for_slug(slug, config_path)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"clip_gen: {clip_summary}\n---\n")
        if _sidecar_safe:
            _write_dataset_hash(slug, cur_hash)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"sidecar: dataset hash = {cur_hash}\n---\n")
        else:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(
                    f"sidecar: NOT updated — mixed dataset (pre-sidecar={_pre_sidecar} "
                    f"!= cur_hash={cur_hash}, existing wavs={_pre_wav_count}). "
                    f"Next preflight will still detect drift.\n---\n"
                )
    except Exception as e:  # noqa: BLE001
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"clip_gen FAILED: {type(e).__name__}: {e}\n---\n")

    # 4b. Ensure fp_val_features.npy exists. OWW's training step needs a
    # 2D (T, 96) feature array from continuous "false-positive" audio —
    # the WSL setup ships this; on Windows we compute it ad-hoc from a
    # short chunk of LibriSpeech speech that isn't the wake phrase.
    try:
        fp_count = _ensure_fp_val_features(slug)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"fp_val_features: {fp_count} frames\n---\n")
    except Exception as e:  # noqa: BLE001
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"fp_val_features FAILED: {type(e).__name__}: {e}\n---\n")

    # 4c. VAD-in-training (optional, per-slug override). Closes the
    # train/test distribution gap — runtime gates wake detection
    # through Silero VAD, so positives that train on silence-padded
    # clips learn the wrong feature distribution. When enabled:
    # trim each clip from first VAD-speech start to last end, re-pad
    # to exactly 2.0s @ 16 kHz mono. Idempotent — writes a
    # `.vad_processed` sidecar marker per slug. Destructive (raw
    # piper output gets overwritten); revert via wipe_stale=True on a
    # subsequent /train.
    from . import vad_preprocess
    if overrides.get("vad_in_training"):
        try:
            vad_results = vad_preprocess.apply_to_slug(slug)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"vad_in_training: {vad_results}\n---\n")
        except Exception as e:  # noqa: BLE001
            with log_path.open("a", encoding="utf-8") as f:
                f.write(
                    f"vad_in_training FAILED: {type(e).__name__}: {e} "
                    f"(continuing without VAD)\n---\n"
                )
    elif vad_preprocess.is_processed(slug):
        # Mixed-state warning: positives are VAD-trimmed from a prior
        # run but this run isn't requesting VAD. Training proceeds (the
        # trimmed positives are still valid wake clips) — but the A/B
        # baseline-vs-VAD signal is muddied. Recommend wiping for a
        # truly raw baseline run.
        with log_path.open("a", encoding="utf-8") as f:
            f.write(
                "(WARN: .vad_processed marker exists but vad_in_training "
                "is off — positives are VAD-trimmed from a prior run. "
                "Pass wipe_stale=true to clear + regenerate raw clips "
                "for a clean baseline retrain.)\n---\n"
            )

    # 5. Spawn launcher → openwakeword.train
    # --augment_clips: compute feature .npy files from positive/negative
    #   clips + RIR + background augmentation. Skipped if .npy already
    #   exists (OWW's own idempotence).
    # --train_model: actually train the model using those .npy features.
    # We don't pass --generate_clips because clip_gen above already
    # populated the bucket dirs; OWW would just re-skip ("~enough exist").
    cmd = [
        sys.executable, "-m", "yz_wakeword_trainer._launcher",
        "--training_config", str(config_path),
        "--augment_clips",
        "--train_model",
    ]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"  # so the launcher's prints + OWW's stdout don't buffer
    env["PYTHONIOENCODING"] = "utf-8"  # Windows default cp1252 stdout chokes on any non-ASCII glyph

    # cwd = wakeword_root is the linchpin of the no-absolute-paths design.
    # The rendered config has `runs/...` and `corpora/...` lines; OWW's
    # `os.scandir(path)` resolves them against cwd. With cwd here, the
    # template stays universally portable — no per-user path strings.
    settings.wakeword_root.mkdir(parents=True, exist_ok=True)
    cwd = str(settings.wakeword_root)
    kwargs: dict[str, Any] = {
        "stdout": log_path.open("a", encoding="utf-8"),
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
        "cwd": cwd,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    p = subprocess.Popen(cmd, **kwargs)

    job = {
        "job_id": state.CURRENT_JOB_ID,
        "slug": slug,
        "pid": p.pid,
        "started_at": time.time(),
        "log_path": str(log_path),
        "state": "running",
        "cmd": " ".join(cmd),
    }
    state.save_job(job)
    return state.CURRENT_JOB_ID


def _process_alive(pid: int) -> bool:
    """True iff `pid` refers to a live, non-zombie process.

    On Windows: `os.kill(pid, 0)` succeeds iff the process exists and is
    running. No zombies on Windows.

    On Linux: `os.kill(pid, 0)` ALSO succeeds for zombies (process exists
    in the kernel until parent reaps). Without further checks the reaper
    would think a finished training subprocess is still running and never
    transition the job to done. We additionally read /proc/<pid>/status
    and treat State=Z (zombie) or State=X (dead) as "not alive".
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("State:"):
                        state_field = line.split(":", 1)[1].strip()
                        state_char = state_field[0] if state_field else "R"
                        return state_char not in ("Z", "X")
        except (OSError, IOError):
            return False  # /proc entry gone → process really gone
    return True


def _try_reap_child(pid: int) -> bool:
    """If `pid` is our child and has exited, waitpid() it to clear the
    zombie + free its kernel slot. No-op (returns False) if pid isn't
    our child, hasn't exited yet, or has already been reaped.

    Called from the reaper loop on every tick so a finished training
    subprocess doesn't linger as <defunct> after the satellite stops
    polling /jobs/current.
    """
    if pid <= 0 or sys.platform == "win32":
        return False
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        return reaped_pid == pid
    except (ChildProcessError, OSError):
        # ChildProcessError: not our child (orphan adoption, satellite
        # restart) — kernel cleans the zombie via init reaping eventually.
        # OSError on other failures — nothing we can do here.
        return False


def stop(job: dict) -> tuple[bool, str]:
    """Cascade-kill the training subprocess tree.

    POSIX: os.killpg(getpgid(pid), SIGTERM); sleep 1; SIGKILL
    Windows: taskkill /F /T /PID — kills the whole process tree
    """
    pid = int(job.get("pid", 0))
    if pid <= 0:
        state.clear_job()
        return True, "no pid"

    try:
        if sys.platform == "win32":
            # /T = tree, /F = force, /PID = target
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        else:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(1)
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
    except Exception as e:  # noqa: BLE001
        return False, f"kill error: {e}"

    # Capture metrics if log shows training completed before the kill
    _maybe_capture_metrics(job)
    state.clear_job()
    return True, "stopped"


# ─────────────────────── log reading + streaming ──────────────────────────

def read_log(job: dict, tail: int = 80, raw: bool = False) -> dict:
    """Return last N lines of the log file. Cleaned by default."""
    log_path = Path(job.get("log_path") or settings.log_path)
    if not log_path.exists():
        return {"log": "", "lines": tail}

    # Fetch 3× requested when cleaning so noise removal still leaves enough signal
    n_fetch = tail * 3 if not raw else tail
    text = _tail_text(log_path, n_fetch)
    if raw:
        return {"log": text, "lines": tail}
    return {"log": log_cleaner.clean(text), "lines": tail}


def _tail_text(path: Path, n_lines: int) -> str:
    """Last `n_lines` lines of a file. Pure Python — no `tail` subprocess."""
    try:
        # Small files: just read all
        size = path.stat().st_size
        if size < 256 * 1024:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            return "".join(lines[-n_lines:])
        # Large files: seek-from-end strategy
        chunk = 64 * 1024
        with path.open("rb") as f:
            offset = max(0, size - chunk * 8)
            f.seek(offset)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        return "".join(lines[-n_lines:])
    except OSError:
        return ""


async def stream_log(job: dict) -> AsyncIterator[str]:
    """Async tail-f. Yields newly-appended file chunks until the child exits.

    Polls the file every 0.5s — light overhead, no OS-specific inotify needed.
    """
    log_path = Path(job.get("log_path") or settings.log_path)
    pid = int(job.get("pid", 0))
    pos = 0
    if log_path.exists():
        pos = log_path.stat().st_size

    while _process_alive(pid):
        await asyncio.sleep(0.5)
        try:
            sz = log_path.stat().st_size
        except OSError:
            break
        if sz > pos:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
            yield chunk
            pos = sz

    # One final flush — anything written between last poll and process exit
    try:
        sz = log_path.stat().st_size
        if sz > pos:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                yield f.read()
    except OSError:
        pass


# ───────────────────── metrics capture on exit ────────────────────────────

def _maybe_capture_metrics(job: dict) -> None:
    """If the run produced complete `Final Model …` metrics, persist a
    rich history row to the model meta.

    Prefers `runs/<slug>/training_metrics.log` (the launcher's clean
    FileHandler-fed file) over the noisy main training log, because
    tqdm-on-stderr buffering corrupts the tail of the main log and the
    last few `logging.info` lines (the metrics) get clobbered.
    """
    slug = job.get("slug")
    if not slug:
        return
    m = metrics.parse_file(settings.runs_dir / slug / "training_metrics.log")
    if m is None:
        # Fallback: try the noisy main log (some prior runs may only have this).
        log_path = Path(job.get("log_path") or settings.log_path)
        if log_path.exists():
            try:
                m = metrics.parse(log_path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                m = None
    if m is None:
        return

    # Build a rich history row. negatives_hash + count makes "did the
    # negatives actually change between runs?" answerable in the compare
    # UI without diffing arrays. Config snapshot is the smallest useful
    # subset of the rendered yaml — enough for "was this run done at
    # n_samples=200 (smoke) vs 50000 (real)?".
    import hashlib
    meta_now = state.load_model(slug) or {}
    negatives = meta_now.get("negatives") or []
    neg_blob = "\n".join(negatives).encode("utf-8")
    neg_hash = "sha1:" + hashlib.sha1(neg_blob).hexdigest()[:8]
    onnx_path = settings.runs_dir / f"{slug}.onnx"
    onnx_size = onnx_path.stat().st_size if onnx_path.exists() else 0
    started_at = float(job.get("started_at") or 0.0)
    elapsed = max(0.0, time.time() - started_at) if started_at else 0.0

    cfg_snap: dict[str, Any] = {}
    try:
        import yaml as _y
        cfg = _y.safe_load(settings.runtime_config.read_text(encoding="utf-8")) or {}
        cfg_snap = {k: cfg.get(k) for k in ("n_samples", "n_samples_val", "augmentation_rounds", "steps")}
    except Exception:
        pass

    from .. import __version__
    run = {
        "ts": time.time(),
        "config": cfg_snap,
        "negatives_count": len(negatives),
        "negatives_hash": neg_hash,
        "elapsed_seconds": round(elapsed, 1),
        "metrics": m,
        "onnx_size": onnx_size,
        "satellite_version": __version__,
    }
    state.record_metrics(slug, m, history_row=run)


def _scan_for_orphan_launcher() -> tuple[int, str] | None:
    """Find a running yz_wakeword_trainer._launcher process whose
    parent is no longer this satellite (orphaned across satellite
    restart). Returns (pid, training_config_path) or None."""
    if sys.platform == "win32":
        return None  # would need wmic / pywin32; Linux is the practical case
    try:
        proc_root = Path("/proc")
        if not proc_root.exists():
            return None
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().split(b"\x00")
            except (OSError, PermissionError):
                continue
            joined = b" ".join(cmdline).decode("utf-8", errors="replace")
            if "yz_wakeword_trainer._launcher" not in joined:
                continue
            # Skip dataloader workers — only the top launcher has the
            # _launcher in argv[1] (`-m yz_wakeword_trainer._launcher`).
            # Workers spawn via torch's spawn helper and have same cmdline
            # though — distinguish by ppid: top launcher's ppid is satellite-
            # or-init; worker's ppid is the top launcher.
            ppid = _read_ppid(int(entry.name))
            if ppid is None:
                continue
            # If ppid points at another launcher with same cmdline, this
            # is a worker — skip.
            try:
                parent_cmdline = (Path("/proc") / str(ppid) / "cmdline").read_bytes().split(b"\x00")
                parent_joined = b" ".join(parent_cmdline).decode("utf-8", errors="replace")
                if "yz_wakeword_trainer._launcher" in parent_joined:
                    continue  # parent is the real launcher; we're a worker
            except (OSError, PermissionError):
                pass  # parent gone — we're the top of the tree
            # Found a top-level launcher. Extract --training_config.
            cfg = ""
            for i, part in enumerate(cmdline):
                if part == b"--training_config" and i + 1 < len(cmdline):
                    cfg = cmdline[i + 1].decode("utf-8", errors="replace")
                    break
            return int(entry.name), cfg
    except OSError:
        pass
    return None


def _read_ppid(pid: int) -> int | None:
    try:
        stat = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8")
    except (OSError, PermissionError):
        return None
    for line in stat.splitlines():
        if line.startswith("PPid:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _slug_from_runtime_config(cfg_path: str) -> str:
    """Read model_name from a rendered runtime config so adoption can
    populate slug. Empty string if unreadable."""
    try:
        import re as _re
        text = Path(cfg_path).read_text(encoding="utf-8")
        m = _re.search(r"^model_name:\s*(\S+)", text, _re.MULTILINE)
        return m.group(1) if m else ""
    except (OSError, AttributeError):
        return ""


def adopt_orphans_if_any() -> dict | None:
    """If a launcher process is running but we have no session file,
    write a session pointing at it. Called once at satellite startup so
    restarts don't lose in-flight trainings. Returns the adopted job dict
    or None."""
    if state.load_job():
        return None  # we already have a session — nothing to adopt
    found = _scan_for_orphan_launcher()
    if not found:
        return None
    pid, cfg_path = found
    slug = _slug_from_runtime_config(cfg_path) if cfg_path else ""
    job = {
        "job_id": state.CURRENT_JOB_ID,
        "slug": slug,
        "pid": pid,
        "started_at": time.time(),  # unknown, approximate (used for display only)
        "log_path": str(settings.log_path),
        "state": "running",
        "cmd": f"adopted orphan launcher (pid {pid})",
        "adopted": True,
    }
    state.save_job(job)
    return job


async def reaper_loop() -> None:
    """Background coroutine: watch the currently-running job, capture
    metrics when it exits, clear state. Kicked off at server startup.
    Also performs orphan adoption on first tick — so a satellite that
    restarted mid-training automatically reattaches to the launcher.

    Each tick:
      1. Try to actively `waitpid(pid, WNOHANG)` so a finished launcher
         subprocess doesn't linger as <defunct> on Linux.
      2. Probe `_process_alive(pid)` — Linux-zombie-aware.
      3. On detected exit: parse metrics → append history row → clear
         the session file.
    """
    # One-shot adoption on first tick
    try: adopt_orphans_if_any()
    except Exception: pass
    while True:
        job = state.load_job()
        if job and job.get("state") == "running":
            pid = int(job.get("pid", 0))
            if pid:
                _try_reap_child(pid)  # No-op if not our child / still running
                if not _process_alive(pid):
                    _maybe_capture_metrics(job)
                    state.clear_job()
        await asyncio.sleep(2.0)


# Surface a few utilities to server.py that don't fit elsewhere
__all__ = [
    "start", "stop", "read_log", "stream_log",
    "reaper_loop",
    "gpu_info", "gpu_free_mb", "python_version",
]
