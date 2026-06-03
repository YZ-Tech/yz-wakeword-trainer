"""Corpora download + status for the openWakeWord training pipeline.

OWW's training step depends on three external audio corpora — RIR (room
impulse responses, for reverb augmentation), LibriSpeech train-clean-100
(speech backgrounds), and FMA-small (music backgrounds). These are big
(~13.5 GB combined) and live outside the satellite's package.

This module exposes:
- a static `CATALOG` of corpora the satellite knows how to fetch
- `status_all()` returning per-corpus state for the UI
- `download(name)` / `cancel(name)` lifecycle
- `is_ready()` for the trainer to gate on before launching OWW

Corpora live under `settings.corpora_dir/<layout>` matching what the YAML's
`rir_paths` and `background_paths` resolve to. The trainer rewrites those
config paths to absolute references during render.

Download model: streamed HTTP via `httpx`, written to a `.partial` file,
renamed on completion, then extracted in-place. Cancellation is cooperative
via a per-corpus event; the worker checks it each chunk + each archive
member.

NOT covered here: `loom_room` (user's own recordings — no upstream URL).
The trainer treats loom_room as optional; if the dir is missing, training
proceeds with the other backgrounds only.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import httpx

from ..settings import settings

log = logging.getLogger(__name__)


# ───────────────────────────── catalog ────────────────────────────────────


@dataclass(frozen=True)
class CorpusSpec:
    """Static description of one corpus.

    `dest_subdir` is relative to settings.corpora_dir — the layout matches
    what OWW's YAML expects under `<output_dir>/.../backgrounds` /
    `.../rirs`, so the trainer can build absolute paths by joining.

    `verify_subpath` is the path (under dest_subdir) that must exist + be
    non-empty for the corpus to count as "present". Different archive
    layouts (e.g. LibriSpeech extracts a `LibriSpeech/train-clean-100/`
    subtree) mean we can't just check that dest_subdir exists.
    """
    name: str
    label: str
    url: str
    archive: str  # "zip" | "tar.gz"
    dest_subdir: str
    verify_subpath: str
    expected_bytes: int  # informational only — server doesn't validate exact match


CATALOG: dict[str, CorpusSpec] = {
    "mit_ir": CorpusSpec(
        name="mit_ir",
        label="MIT IR Survey (room impulse responses)",
        # Canonical URL — note `IRMAudio` (not `IR_Survey`) and HTTPS. The
        # archive ships at 32 kHz; the trainer downsamples to 16 kHz before
        # use (see tools/wakeword/_resample_rirs.py for the reference).
        url="https://mcdermottlab.mit.edu/Reverb/IRMAudio/Audio.zip",
        archive="zip",
        dest_subdir="rirs/mit_ir_survey",
        verify_subpath="rirs/mit_ir_survey/Audio",
        expected_bytes=290_000_000,
    ),
    "librispeech": CorpusSpec(
        name="librispeech",
        label="LibriSpeech train-clean-100 (speech background)",
        url="https://www.openslr.org/resources/12/train-clean-100.tar.gz",
        archive="tar.gz",
        dest_subdir="backgrounds",
        verify_subpath="backgrounds/LibriSpeech/train-clean-100",
        expected_bytes=6_300_000_000,
    ),
    "fma_small": CorpusSpec(
        name="fma_small",
        label="FMA-small (music background)",
        url="https://os.unil.cloud.switch.ch/fma/fma_small.zip",
        archive="zip",
        dest_subdir="backgrounds",
        verify_subpath="backgrounds/fma_small",
        expected_bytes=7_200_000_000,
    ),
}


# ───────────────────────────── runtime state ──────────────────────────────


@dataclass
class CorpusState:
    """Mutable per-corpus runtime view. Read by /corpora/status."""
    name: str
    phase: str = "idle"          # idle | downloading | extracting | complete | error | cancelled
    bytes_done: int = 0
    bytes_total: int = 0         # 0 until Content-Length seen
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


_states: dict[str, CorpusState] = {n: CorpusState(name=n) for n in CATALOG}
_workers: dict[str, threading.Thread] = {}
_lock = threading.Lock()


# ───────────────────────────── status ─────────────────────────────────────


def _verify(spec: CorpusSpec) -> tuple[bool, int]:
    """Return (present, bytes_on_disk) for a corpus. 'Present' = the
    verify_subpath dir exists and contains at least one file (any depth)."""
    p = settings.corpora_dir / spec.verify_subpath
    if not p.exists():
        return False, 0
    total = 0
    found_any = False
    for child in p.rglob("*"):
        if child.is_file():
            found_any = True
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return found_any, total


def status_one(name: str) -> dict:
    spec = CATALOG[name]
    present, on_disk = _verify(spec)
    st = _states[name]
    # If a corpus is fully present on disk but the in-memory state hasn't
    # caught up (cold start), reflect that. The on_disk byte count is a
    # useful indicator that downloads are not all-or-nothing.
    phase = st.phase
    if present and phase in ("idle", "complete"):
        phase = "complete"
    return {
        "name": name,
        "label": spec.label,
        "url": spec.url,
        "dest": str((settings.corpora_dir / spec.verify_subpath).resolve()),
        "phase": phase,
        "present": present,
        "bytes_on_disk": on_disk,
        "bytes_done": st.bytes_done,
        "bytes_total": st.bytes_total or spec.expected_bytes,
        "expected_bytes": spec.expected_bytes,
        "started_at": st.started_at,
        "finished_at": st.finished_at,
        "error": st.error,
    }


def status_all() -> list[dict]:
    return [status_one(n) for n in CATALOG]


def is_ready() -> dict:
    """Are all required corpora present? Returns {ready, missing: [...]}.
    The trainer should refuse to start if not ready."""
    missing = [n for n in CATALOG if not _verify(CATALOG[n])[0]]
    return {"ready": not missing, "missing": missing}


# ───────────────────────────── download + extract ─────────────────────────


CHUNK_BYTES = 1 << 20  # 1 MiB


def _download_stream(spec: CorpusSpec, dest_partial: Path, state: CorpusState,
                     emit: Callable[[], None]) -> None:
    """Stream the archive into `dest_partial`. Updates state.bytes_done +
    state.bytes_total. Honors state.cancel_event between chunks. Raises
    on HTTP error; caller handles the state transition."""
    settings.corpora_dir.mkdir(parents=True, exist_ok=True)
    dest_partial.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", spec.url, follow_redirects=True, timeout=None) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", "0") or 0)
        state.bytes_total = total or spec.expected_bytes
        with dest_partial.open("wb") as f:
            last_emit = time.time()
            for chunk in resp.iter_bytes(chunk_size=CHUNK_BYTES):
                if state.cancel_event.is_set():
                    raise _Cancelled()
                f.write(chunk)
                state.bytes_done += len(chunk)
                now = time.time()
                if now - last_emit > 0.5:
                    emit()
                    last_emit = now
            emit()


def _extract(spec: CorpusSpec, archive_path: Path, state: CorpusState,
             emit: Callable[[], None]) -> None:
    """Extract zip/tar.gz into settings.corpora_dir/spec.dest_subdir.
    Cooperative-cancellable between members. Progress is reported as
    member-count percentage (bytes_done = current member index)."""
    dest = settings.corpora_dir / spec.dest_subdir
    dest.mkdir(parents=True, exist_ok=True)
    if spec.archive == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            members = zf.namelist()
            state.bytes_total = len(members)
            for i, m in enumerate(members):
                if state.cancel_event.is_set():
                    raise _Cancelled()
                zf.extract(m, dest)
                state.bytes_done = i + 1
                if i % 50 == 0:
                    emit()
            emit()
    elif spec.archive == "tar.gz":
        with tarfile.open(archive_path, "r:gz") as tf:
            members = tf.getmembers()
            state.bytes_total = len(members)
            for i, m in enumerate(members):
                if state.cancel_event.is_set():
                    raise _Cancelled()
                tf.extract(m, dest)
                state.bytes_done = i + 1
                if i % 100 == 0:
                    emit()
            emit()
    else:
        raise RuntimeError(f"unknown archive format {spec.archive!r}")


class _Cancelled(Exception):
    """Internal sentinel for cooperative cancellation."""


_POST_INSTALL: dict[str, Callable[["CorpusState", Callable[[], None]], None]] = {}


def _resample_mit_ir(state: "CorpusState", emit: Callable[[], None]) -> None:
    """MIT IR survey ships at 32 kHz. OWW's augment_clips convolves RIRs at
    the audio's sample rate AND rebinds its local `sr` to the RIR's rate
    after the first reverberation — breaking the per-clip SR check on the
    next batch. Resample all RIRs to 16 kHz in place so OWW sees consistent
    rates throughout training."""
    import torchaudio
    import torchaudio.functional as F
    target_sr = 16000
    rir_dir = settings.corpora_dir / "rirs" / "mit_ir_survey" / "Audio"
    if not rir_dir.exists():
        return
    wavs = sorted(rir_dir.glob("*.wav"))
    state.bytes_total = len(wavs)
    state.bytes_done = 0
    converted = 0
    for w in wavs:
        if state.cancel_event.is_set():
            raise _Cancelled()
        info = torchaudio.info(str(w))
        if info.sample_rate != target_sr:
            waveform, sr = torchaudio.load(str(w))
            waveform = F.resample(waveform, orig_freq=sr, new_freq=target_sr)
            torchaudio.save(str(w), waveform, sample_rate=target_sr)
            converted += 1
        state.bytes_done += 1
        if state.bytes_done % 25 == 0:
            emit()
    log.info("mit_ir post-install: resampled %d/%d RIRs to %d Hz",
             converted, len(wavs), target_sr)


_POST_INSTALL["mit_ir"] = _resample_mit_ir


def _worker(name: str, emit: Callable[[], None]) -> None:
    """Thread body: download → extract → post-install → cleanup. Updates
    the state transitions; emit() pushes a snapshot to subscribers between
    phases."""
    spec = CATALOG[name]
    state = _states[name]
    state.cancel_event.clear()
    state.phase = "downloading"
    state.started_at = time.time()
    state.finished_at = 0.0
    state.error = ""
    state.bytes_done = 0
    state.bytes_total = spec.expected_bytes
    emit()

    archive_name = spec.url.rsplit("/", 1)[-1]
    dest_archive = settings.corpora_dir / archive_name
    dest_partial = dest_archive.with_suffix(dest_archive.suffix + ".partial")

    try:
        _download_stream(spec, dest_partial, state, emit)
        dest_partial.rename(dest_archive)
        state.phase = "extracting"
        state.bytes_done = 0
        emit()
        _extract(spec, dest_archive, state, emit)
        # Post-install hook (e.g. RIR resample). Runs in the "extracting"
        # phase so the UI shows continued progress rather than flipping
        # to "complete" while we're still working.
        post = _POST_INSTALL.get(name)
        if post:
            post(state, emit)
        state.phase = "complete"
        state.finished_at = time.time()
        # Delete the archive after successful extraction to free disk
        try:
            dest_archive.unlink()
        except OSError:
            pass
        emit()
    except _Cancelled:
        state.phase = "cancelled"
        state.finished_at = time.time()
        # Leave .partial behind so a future download can resume manually if
        # we add resume support. For now, just clean up to avoid confusing
        # the user.
        try: dest_partial.unlink(missing_ok=True)
        except OSError: pass
        emit()
    except Exception as e:  # noqa: BLE001
        log.exception("corpora worker %s failed", name)
        state.phase = "error"
        state.error = f"{type(e).__name__}: {e}"
        state.finished_at = time.time()
        emit()
    finally:
        with _lock:
            _workers.pop(name, None)


def start(name: str, emit: Callable[[], None]) -> None:
    """Kick off a download in a worker thread. No-op if already running.
    `emit` is called whenever state advances enough to be worth pushing
    (UI updates)."""
    if name not in CATALOG:
        raise ValueError(f"unknown corpus {name!r}")
    with _lock:
        if name in _workers and _workers[name].is_alive():
            return
        # Already-complete corpora skip re-download.
        present, _ = _verify(CATALOG[name])
        if present:
            _states[name].phase = "complete"
            emit()
            return
        t = threading.Thread(target=_worker, args=(name, emit), daemon=True,
                             name=f"corpora-{name}")
        _workers[name] = t
        t.start()


def cancel(name: str) -> None:
    """Cooperative cancel. Sets the event; the worker checks it between
    chunks/members and raises _Cancelled internally. No-op if idle."""
    if name not in CATALOG:
        return
    _states[name].cancel_event.set()


def start_all(emit: Callable[[], None]) -> None:
    for n in CATALOG:
        start(n, emit)
