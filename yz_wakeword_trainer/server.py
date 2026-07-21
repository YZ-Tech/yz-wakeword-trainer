"""HTTP/WS surface. The CONTRACT between this satellite and any client.

Clients (JarvYZ adapter, CLI, userscripts, your dog) talk to these routes; the
trainer internals stay private. If a route changes shape, that's a satellite
version bump.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .core import corpora, persistent_settings, spike, state, trainer
from .settings import settings

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # was three @app.on_event("startup") hooks — deprecated in favor of
    # lifespan. Order preserved: reaper -> orphan-metric recovery -> loop stash.
    #
    # Reaper: background task that watches the active job and harvests
    # metrics when its subprocess exits. Idempotent — state on disk.
    asyncio.create_task(trainer.reaper_loop())
    # Orphan sweep: ONNX newer than its training_history row -> parse
    # training_metrics.log to fill the gap (failure window between
    # ONNX-export and reaper, 2026-05-28). Cheap; runs once per boot.
    try:
        recovered = trainer.recover_metrics_all()
        if recovered:
            print(f"[startup] recovered orphan metrics for: {recovered}")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] metric recovery skipped: {e}")
    # Stash the running loop so corpora worker threads can marshal
    # snapshot pushes back onto it.
    _capture_loop()
    yield


app = FastAPI(
    title="wakeword-trainer",
    version=__version__,
    description="Satellite: openWakeWord training as a service.",
    lifespan=_lifespan,
)


# ─────────────────────── request/response models ──────────────────────────

class TrainRequest(BaseModel):
    slug: str
    config_overrides: dict[str, Any] | None = None
    # Opt-in: wipe stale negative clips + features + .onnx if the slug's
    # negatives_hash has drifted from the last successful training. Default
    # OFF — never auto-wipes. Without this flag the trainer logs a warning
    # and proceeds against whatever's on disk (clip_gen will resume any
    # gaps, but pre-existing clips from a different phrase list will NOT
    # be replaced).
    wipe_stale: bool = False


class JobResponse(BaseModel):
    job_id: str


class CorpusRequest(BaseModel):
    corpus: str  # one of CATALOG keys, or "all"


class SettingsPatch(BaseModel):
    """Fields the UI is allowed to mutate. Anything `None` is left as-is;
    anything present overwrites (full replace for list fields)."""
    extra_background_paths: list[str] | None = None
    global_negative_phrases: list[str] | None = None


class CreateModelRequest(BaseModel):
    phrase: str
    slug: str | None = None
    language: str = "en"


class CloneModelRequest(BaseModel):
    slug: str  # destination slug


class NegativesRequest(BaseModel):
    phrases: list[str]


class RecordStartRequest(BaseModel):
    minutes: float = 10.0
    device: str | None = None  # device name; None = use JarvYZ settings or PortAudio default


class SpikeStartRequest(BaseModel):
    slug: str
    device: int | str | None = None  # PortAudio index OR name substring; None = auto


_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_BG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.wav$")


def _slugify(text: str) -> str:
    """Filesystem-safe slug. 'hey Aurora!' → 'hey_aurora'.
    Same convention as JarvYZ-side wakeword_dev._slugify."""
    s = re.sub(r"[^a-z0-9_]+", "_", text.strip().lower()).strip("_")
    return s or "model"


# ───────────────────────────── routes ─────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "gpu": trainer.gpu_info(),
        "gpu_free_mb": trainer.gpu_free_mb(),
        "python": trainer.python_version(),
    }


@app.get("/train/preflight/{slug}")
def train_preflight(slug: str) -> dict:
    """Pre-train probe: would starting a train now trigger a wipe of stale
    negative clips? Returns {would_wipe, cur_hash, last_hash, neg_train_clips,
    neg_test_clips, npy_features, onnx_size}. The UI calls this before
    /train so it can show a confirm dialog instead of silently destroying
    on-disk data."""
    return trainer.preflight(slug)


@app.post("/train", response_model=JobResponse)
def train_start(req: TrainRequest, background: BackgroundTasks) -> JobResponse:
    """Dispatch a training job. Returns immediately with a "preparing"
    session marker; the actual work (clip_gen synthesis, fp_val features,
    OWW subprocess spawn) runs in a FastAPI BackgroundTask so the HTTP
    response doesn't block.

    Without this, the client's httpx ReadTimeout fires within seconds
    for any fresh-slug train where clip_gen has to synthesize 100k+
    WAVs — that's minutes of work the request can't be held open for.

    409 if any job is already present (preparing OR running) so accidental
    double-clicks don't fork two concurrent training runs.
    """
    if state.load_job() is not None:
        raise HTTPException(409, "a training job is already running")
    # Fold wipe_stale into overrides so trainer.start sees it consistently
    # alongside config_overrides keys. Default False = never auto-wipe.
    merged_overrides = dict(req.config_overrides or {})
    merged_overrides["wipe_stale"] = req.wipe_stale
    trainer.dispatch(slug=req.slug, overrides=merged_overrides,
                     background=background)
    return JobResponse(job_id=state.CURRENT_JOB_ID)


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = state.get_job(job_id)
    if not job:
        raise HTTPException(404, f"unknown job {job_id}")
    return job


@app.get("/jobs/{job_id}/log")
def job_log(job_id: str, tail: int = 80, raw: bool = False) -> dict:
    job = state.get_job(job_id)
    if not job:
        raise HTTPException(404, f"unknown job {job_id}")
    return trainer.read_log(job, tail=tail, raw=raw)


@app.websocket("/jobs/{job_id}/log/stream")
async def job_log_stream(ws: WebSocket, job_id: str) -> None:
    """Server-pushed log chunks as they arrive. Closes when the job ends."""
    job = state.get_job(job_id)
    if not job:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        async for chunk in trainer.stream_log(job):
            await ws.send_json({"chunk": chunk})
    except WebSocketDisconnect:
        pass


@app.post("/jobs/{job_id}/stop")
def job_stop(job_id: str) -> dict:
    job = state.get_job(job_id)
    if not job:
        raise HTTPException(404, f"unknown job {job_id}")
    ok, msg = trainer.stop(job)
    return {"ok": ok, "message": msg}


@app.get("/models")
def list_models() -> list[dict]:
    return state.list_models()


@app.delete("/models/{slug}")
def delete_model(slug: str) -> dict:
    state.delete_model(slug)
    return {"ok": True}


# ───────────────────────────── corpora ────────────────────────────────────


# Progress-event broadcaster: each connected WS gets snapshots when state
# changes. The corpora module calls `emit_snapshot()` between phases /
# every 500 ms during streaming.
_corpora_subs: set[asyncio.Queue[dict]] = set()
_corpora_loop: asyncio.AbstractEventLoop | None = None


def _capture_loop() -> None:
    """Stash the running loop reference at startup so the corpora worker
    threads (off-loop) can schedule snapshot pushes onto it."""
    global _corpora_loop
    _corpora_loop = asyncio.get_running_loop()


def _emit_corpora_snapshot() -> None:
    """Called from worker threads. Marshals the snapshot push back to the
    event loop so the asyncio.Queues can receive it safely.

    Includes BOTH `corpora` (per-corpus phase/bytes) AND `ready` (the
    aggregate {ready, missing}) so subscribers can update a single cache
    without doing a follow-up REST call to derive ready. Earlier
    versions sent only `corpora` — JarvYZ's bridge would merge that into
    its cache, leaving `ready` stale across satellite restarts (the
    bug Yeon saw 2026-05-30: 3 green rows + "3 missing" badge after
    satellite kill+respawn)."""
    snap = {"corpora": corpora.status_all(), "ready": corpora.is_ready()}
    if _corpora_loop is None or _corpora_loop.is_closed():
        return
    def _push() -> None:
        for q in list(_corpora_subs):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                pass
    _corpora_loop.call_soon_threadsafe(_push)


@app.get("/corpora/status")
def corpora_status() -> dict:
    return {"corpora": corpora.status_all(), "ready": corpora.is_ready()}


@app.post("/corpora/download")
def corpora_download(req: CorpusRequest) -> dict:
    if req.corpus == "all":
        corpora.start_all(_emit_corpora_snapshot)
    elif req.corpus in corpora.CATALOG:
        corpora.start(req.corpus, _emit_corpora_snapshot)
    else:
        raise HTTPException(400, f"unknown corpus {req.corpus!r}")
    return {"ok": True, "corpora": corpora.status_all()}


@app.post("/corpora/cancel")
def corpora_cancel(req: CorpusRequest) -> dict:
    if req.corpus == "all":
        for n in corpora.CATALOG:
            corpora.cancel(n)
    else:
        corpora.cancel(req.corpus)
    return {"ok": True}


# ──────────────────────────── settings ────────────────────────────────────


@app.get("/settings")
def get_settings() -> dict:
    """Current UI-editable settings. Reflects whatever's on the in-memory
    `settings` instance — env defaults overlaid by the persistent JSON."""
    return persistent_settings.dump()


@app.patch("/settings")
def patch_settings(body: SettingsPatch) -> dict:
    """Mutate + persist. Validates each path field exists before saving
    so the UI can show a clean error rather than silently ignoring an
    invalid entry."""
    from pathlib import Path as _P
    if body.extra_background_paths is not None:
        bad = [p for p in body.extra_background_paths if p and not _P(p).is_dir()]
        if bad:
            raise HTTPException(400, f"not a directory: {bad}")
        settings.extra_background_paths = [_P(p) for p in body.extra_background_paths if p]
    if body.global_negative_phrases is not None:
        # Sanitize: strip, dedupe, drop empties. Same as PUT /negatives.
        seen: set[str] = set()
        cleaned: list[str] = []
        for p in (s.strip() for s in body.global_negative_phrases):
            if p and p not in seen:
                seen.add(p)
                cleaned.append(p)
        settings.global_negative_phrases = cleaned
    persistent_settings.save()
    return persistent_settings.dump()


@app.websocket("/corpora/progress")
async def corpora_progress(ws: WebSocket) -> None:
    """Server-pushed corpora snapshots. Send one on connect (initial state)
    + one every time the module emits a change."""
    await ws.accept()
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=32)
    _corpora_subs.add(q)
    try:
        await ws.send_json({"corpora": corpora.status_all(), "ready": corpora.is_ready()})
        while True:
            snap = await q.get()
            await ws.send_json(snap)
    except WebSocketDisconnect:
        pass
    finally:
        _corpora_subs.discard(q)


@app.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    """Unified event stream for JarvYZ's generic satellite event bridge
    (`satellite_events._bridge_loop`). Re-frames every corpora-progress
    snapshot as a `wakeword_state` event so the core bus — and thus the
    embedded trainer UI — refreshes `/status` live during downloads.

    This replaces the bespoke per-route WS bridge that used to live in
    JarvYZ's `wakeword_dev_satellite.py`. The payload is intentionally
    minimal: the UI refetches `/status` on any `wakeword_state`, and the
    satellite's own `/status` already carries the fresh corpora block, so
    there's nothing to marshal through the event itself. Standalone UIs
    keep using `/corpora/progress` directly; this endpoint exists purely
    for the JarvYZ bridge's one-WS-per-satellite contract."""
    await ws.accept()
    await ws.send_json({"kind": "hello"})
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=32)
    _corpora_subs.add(q)
    try:
        while True:
            await q.get()  # a corpora snapshot changed
            await ws.send_json({"event": "wakeword_state", "kind": "corpora"})
    except WebSocketDisconnect:
        pass
    finally:
        _corpora_subs.discard(q)


# ─────────────────────── models — create / clone / download ───────────────


@app.post("/models")
def create_model(req: CreateModelRequest) -> dict:
    """Create a per-model meta JSON. Does NOT start training — pure registry
    create. Slug auto-derives from phrase if absent."""
    phrase = req.phrase.strip()
    if not phrase:
        raise HTTPException(400, "phrase required")
    slug = (req.slug or "").strip() or _slugify(phrase)
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, f"invalid slug {slug!r}")
    if state.load_model(slug) is not None:
        raise HTTPException(409, f"model {slug!r} already exists")
    state.save_model({
        "slug": slug,
        "phrase": phrase,
        "language": req.language,
        "created_at": time.time(),
    })
    return {"ok": True, "slug": slug}


@app.post("/models/{slug}/clone")
def clone_model(slug: str, req: CloneModelRequest) -> dict:
    """Shallow clone: copy phrase + language + negatives into a new slug.
    Fresh created_at, NO metrics / training_history / runs — the clone
    trains from scratch on first /train."""
    src = state.load_model(slug)
    if src is None:
        raise HTTPException(404, f"unknown model {slug!r}")
    dst = req.slug.strip()
    if not _SLUG_RE.match(dst):
        raise HTTPException(400, f"invalid destination slug {dst!r}")
    if state.load_model(dst) is not None:
        raise HTTPException(409, f"model {dst!r} already exists")
    state.save_model({
        "slug": dst,
        "phrase": src.get("phrase", ""),
        "language": src.get("language", "en"),
        "negatives": list(src.get("negatives", [])),
        "created_at": time.time(),
        "cloned_from": slug,
    })
    return {"ok": True, "slug": dst}


@app.get("/models/{slug}/onnx")
def download_onnx(slug: str) -> FileResponse:
    """Stream the trained .onnx with Content-Disposition: attachment so
    the browser triggers a download. Used in standalone mode where there's
    no JarvYZ to deploy to."""
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, "invalid slug")
    target = settings.runs_dir / f"{slug}.onnx"
    if not target.exists():
        raise HTTPException(404, f"no .onnx for {slug!r}")
    return FileResponse(
        path=target,
        media_type="application/octet-stream",
        filename=f"{slug}.onnx",
    )


# ─────────────────────── negatives — global + per-model ───────────────────


@app.get("/negatives")
def get_global_negatives() -> dict:
    """Read the current global custom_negative_phrases list. Returns the
    same shape the UI's setter expects ({phrases: [str]}) so the dialog
    can round-trip cleanly."""
    return {"phrases": list(settings.global_negative_phrases)}


@app.get("/models/{slug}/negatives")
def get_model_negatives(slug: str) -> dict:
    """Read the per-slug negatives list from the model meta JSON."""
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, "invalid slug")
    meta = state.load_model(slug)
    if meta is None:
        raise HTTPException(404, f"no meta for slug {slug!r}")
    return {"phrases": list(meta.get("negatives", []))}


@app.put("/negatives")
def set_global_negatives(req: NegativesRequest) -> dict:
    """Set the global custom_negative_phrases list. Persisted via
    persistent_settings; the trainer renders it into the per-job config."""
    # Sanitize: strip, dedupe, drop empties.
    seen: set[str] = set()
    cleaned: list[str] = []
    for p in (s.strip() for s in req.phrases):
        if p and p not in seen:
            seen.add(p)
            cleaned.append(p)
    settings.global_negative_phrases = cleaned
    persistent_settings.save()
    return {"ok": True, "count": len(cleaned)}


@app.put("/models/{slug}/negatives")
def set_model_negatives(slug: str, req: NegativesRequest) -> dict:
    """Set per-model negative phrases. Stored in the slug's meta JSON;
    rendered into the per-job config alongside the globals."""
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, "invalid slug")
    meta = state.load_model(slug)
    if meta is None:
        raise HTTPException(404, f"no meta for slug {slug!r}")
    meta["negatives"] = [str(p).strip() for p in req.phrases if str(p).strip()]
    state.save_model(meta)
    return {"ok": True, "count": len(meta["negatives"])}


# ─────────────────────── backgrounds (room recordings) ────────────────────


def _bg_dir() -> Path:
    return settings.corpora_dir / "backgrounds" / "loom_room"


@app.get("/backgrounds")
def list_backgrounds() -> list[dict]:
    """List loom_room ambient WAVs. Newest first."""
    d = _bg_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for p in d.glob("*.wav"):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({"name": p.name, "size_bytes": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


@app.delete("/backgrounds/{name}")
def delete_background(name: str) -> dict:
    if not _BG_NAME_RE.match(name):
        raise HTTPException(400, "invalid name")
    target = _bg_dir() / name
    deleted = False
    if target.exists():
        try:
            target.unlink()
            deleted = True
        except OSError as e:
            raise HTTPException(500, f"delete failed: {e}") from e
    return {"ok": True, "deleted": deleted, "name": name}


# ─────────────────────── record (room ambient capture) ────────────────────


def _record_log_path() -> Path:
    return settings.wakeword_root / "record.log"


@app.post("/record/start")
def record_start(req: RecordStartRequest) -> dict:
    """Spawn the ambient-room capture as a subprocess. Returns the pid
    + the log path the UI can tail. 409 if a record is already running."""
    import subprocess
    if state.record_alive():
        raise HTTPException(409, "a record session is already running")
    minutes = max(0.5, min(120.0, float(req.minutes)))
    log_path = _record_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate before each run so the UI tails the current session, not stale.
    log_path.write_text("", encoding="utf-8")
    cmd: list[str] = [
        sys.executable, "-m", "yz_wakeword_trainer.core.record",
        "--minutes", str(minutes),
    ]
    if req.device:
        cmd += ["--device", req.device]
    log_fh = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(settings.wakeword_root),
    )
    state.save_record({
        "pid": proc.pid,
        "minutes": minutes,
        "device": req.device,
        "started_at": time.time(),
        "cmd": " ".join(cmd),
        "log_path": str(log_path),
    })
    return {"ok": True, "pid": proc.pid, "minutes": minutes}


@app.post("/record/stop")
def record_stop() -> dict:
    """Send SIGTERM to the active record subprocess. The capture loop
    catches it via the subprocess's normal handler — buffered audio that
    hasn't been flushed yet is lost (small, <1s)."""
    import os
    import signal
    job = state.load_record()
    if not job:
        return {"ok": True, "message": "no record session"}
    pid = int(job.get("pid", 0))
    if pid <= 0:
        state.clear_record()
        return {"ok": True, "message": "no pid"}
    try:
        os.kill(pid, signal.SIGTERM)
        # Best-effort wait so callers see a clean stopped state if they
        # immediately GET /record/status.
        for _ in range(20):  # up to ~2s
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (ProcessLookupError, OSError):
                break
    except (ProcessLookupError, OSError):
        pass
    state.clear_record()
    return {"ok": True, "stopped_pid": pid}


@app.get("/record/status")
def record_status() -> dict:
    job = state.load_record()
    return {
        "running": state.record_alive(),
        "pid": (job or {}).get("pid"),
        "minutes": (job or {}).get("minutes"),
        "started_at": (job or {}).get("started_at"),
    }


# ─────────────────── spike (live model test) ─────────────────────────────


@app.post("/spike/start")
def spike_start(req: SpikeStartRequest) -> dict:
    """Start a live model-test session. Loads <runs_dir>/<slug>.onnx and
    opens an audio stream; scores update at ~12.5 Hz (80 ms windows).
    Single-concurrent — 409 if one's running."""
    if not _SLUG_RE.match(req.slug):
        raise HTTPException(400, "invalid slug")
    try:
        return spike.start(req.slug, device=req.device)  # type: ignore[arg-type]
    except RuntimeError as e:
        msg = str(e)
        if "already running" in msg:
            raise HTTPException(409, msg) from e
        if "no .onnx" in msg:
            raise HTTPException(404, msg) from e
        raise HTTPException(500, msg) from e


@app.post("/spike/stop")
def spike_stop() -> dict:
    """Stop the active spike session. Idempotent — returns current state
    (running=False) even if nothing was running."""
    return spike.stop()


@app.get("/spike/status")
def spike_status() -> dict:
    """Snapshot for UI polling: running, slug, latest + peak score, plus
    a tail of recent (ts, score) pairs for sparkline rendering."""
    return spike.status()


@app.get("/audio/devices")
def audio_devices() -> dict:
    """List input audio devices visible to PortAudio. The spike card's
    device dropdown uses this. Returns the raw sounddevice shape so the
    UI can render hostapi-grouped picks. Default = sounddevice''s OS
    default input."""
    import sounddevice as sd
    devs = sd.query_devices()
    # query_devices returns a DeviceList (list of dicts). Filter to inputs.
    inputs = []
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0:
            inputs.append({
                "index": i,
                "name": d.get("name", ""),
                "max_input_channels": d.get("max_input_channels", 0),
                "host_api": d.get("hostapi", 0),
                "default_samplerate": d.get("default_samplerate", 0),
            })
    host_apis = []
    for i, ha in enumerate(sd.query_hostapis()):
        host_apis.append({"index": i, "name": ha.get("name", f"api{i}")})
    try:
        default_input = sd.default.device[0] if sd.default.device else None
        if default_input is None or (isinstance(default_input, int) and default_input < 0):
            default_input = None
    except Exception:
        default_input = None
    return {
        "devices": inputs,
        "host_apis": host_apis,
        "default_input": default_input,
    }


@app.get("/record/log")
def record_log(tail: int = 80) -> dict:
    """Tail the record subprocess's stdout/stderr log. Same shape as
    /jobs/{id}/log so the UI's log-tail hook can share fetcher code."""
    p = _record_log_path()
    if not p.exists():
        return {"log": ""}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(500, f"read failed: {e}") from e
    lines = text.splitlines()
    if tail > 0 and len(lines) > tail:
        lines = lines[-tail:]
    return {"log": "\n".join(lines)}


# ─────────────────────── aggregated status ────────────────────────────────


@app.get("/status")
def status() -> dict:
    """One-shot snapshot covering everything the trainer UI needs to render
    a first paint. Composes models + corpora + backgrounds + active job.
    Equivalent to JarvYZ's _native_snapshot, satellite-side."""
    # Backgrounds summary
    bg_files = list_backgrounds()
    bg_disk_bytes = sum(b["size_bytes"] for b in bg_files)

    # RIRs — count files under corpora/rirs/mit_ir_survey/Audio
    rir_dir = settings.corpora_dir / "rirs" / "mit_ir_survey" / "Audio"
    rir_count = 0
    if rir_dir.exists():
        try:
            rir_count = sum(1 for _ in rir_dir.rglob("*.wav"))
        except OSError:
            rir_count = 0

    # Active training job
    job = state.load_job()
    train_block = {
        "running": bool(job and state.current_job_alive()),
        "slug": (job or {}).get("slug", "") or "",
        "cmd": (job or {}).get("cmd", "") or "",
    }

    # Models — augment each meta with dataset/deployed fields the UI cares about.
    # Shape mirrors what JarvYZ's _native_snapshot composes.
    models = []
    for m in state.list_models():
        slug = m.get("slug", "")
        run_dir = settings.runs_dir / slug
        onnx_path = settings.runs_dir / f"{slug}.onnx"
        onnx_size = 0
        onnx_mtime = 0.0
        if onnx_path.exists():
            try:
                st = onnx_path.stat()
                onnx_size = st.st_size
                onnx_mtime = st.st_mtime
            except OSError:
                pass

        def _count_wavs(d: Path) -> int:
            if not d.exists():
                return 0
            try:
                return sum(1 for _ in d.glob("*.wav"))
            except OSError:
                return 0

        models.append({
            **m,
            "negatives": m.get("negatives", []),
            "has_meta": True,
            "dataset": {
                "positive_train": _count_wavs(run_dir / "positive_train"),
                "positive_test": _count_wavs(run_dir / "positive_test"),
                "negative_train": _count_wavs(run_dir / "negative_train"),
                "negative_test": _count_wavs(run_dir / "negative_test"),
                "wsl_onnx_size": onnx_size,
                "wsl_onnx_mtime": onnx_mtime,
            },
            # Satellite is the producer — it doesn't know about JarvYZ's
            # active-models dir. Standalone has no concept of "deployed",
            # so we report exists=False. The UI's "newer than deployed"
            # badge will simply never light up.
            "deployed": {"exists": False, "path": "", "size_bytes": 0, "mtime": 0},
            "metrics": m.get("metrics"),
            "training_history": m.get("training_history", []),
        })

    return {
        "models": models,
        "train": train_block,
        "record": {
            "running": state.record_alive(),
            "cmd": (state.load_record() or {}).get("cmd", ""),
        },
        "backgrounds": {
            "count": len(bg_files),
            "rirs": rir_count,
            "disk_bytes": bg_disk_bytes,
            "available": _bg_dir().exists(),
            "bg_list": bg_files,
        },
        "corpora": {
            "corpora": corpora.status_all(),
            "ready": corpora.is_ready(),
        },
        "wsl_available": True,  # satellite IS the trainer — always "available" here
    }


# ─────────────────────── static UI (standalone) ───────────────────────────
# Serve the SPA from the bundled static/ dir at the root path, so
# `pip install yz-wakeword-trainer && python -m yz_wakeword_trainer`
# gives a working UI at http://127.0.0.1:9001/ — no JarvYZ required.
#
# Built by `cd ui && npm run build:pages` (Vite outDir points here).
# Skipped if static/ doesn't exist or is empty (dev install without a UI
# build). In that case the satellite still exposes its API; users just
# hit /docs or use a client.
#
# Mount LAST: FastAPI matches routes in registration order, so all the
# JSON/WS routes above take precedence over the catch-all StaticFiles.

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.exists() and any(_static_dir.iterdir()):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")
