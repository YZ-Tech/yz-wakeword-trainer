"""Job + model state. Lives on disk under settings.session_dir / models_dir so
state survives satellite restarts (same property JarvYZ's `_session_*` helpers
gave us, kept here)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..settings import settings

# Single-concurrent: there's always "current" (or nothing). Future: real ids.
CURRENT_JOB_ID = "current"


def _session_file() -> Path:
    return settings.session_dir / "train_session.json"


# ─────────────────────────── job state ────────────────────────────────────

def save_job(job: dict[str, Any]) -> None:
    settings.session_dir.mkdir(parents=True, exist_ok=True)
    _session_file().write_text(json.dumps(job, indent=2), encoding="utf-8")


def load_job() -> dict[str, Any] | None:
    p = _session_file()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def clear_job() -> None:
    p = _session_file()
    if p.exists():
        try: p.unlink()
        except OSError: pass


def current_job_alive() -> bool:
    """Whether a running training job exists — verified by PID probe so we
    self-heal across satellite restarts. If the session file says "running"
    but the PID is gone, return False (and let the reaper clear the file)."""
    import os
    job = load_job()
    if not job or job.get("state") != "running":
        return False
    pid = int(job.get("pid", 0))
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def get_job(job_id: str) -> dict | None:
    if job_id != CURRENT_JOB_ID:
        return None
    return load_job()


# ─────────────────────────── record-session state ─────────────────────────
# Independent of the training-job slot. The two can in principle coexist,
# though in practice the user records OR trains, not both at once (mic vs
# GPU isn't a hard conflict, but audio capture wants the room quiet).


def _record_session_file() -> Path:
    return settings.session_dir / "record_session.json"


def save_record(job: dict[str, Any]) -> None:
    settings.session_dir.mkdir(parents=True, exist_ok=True)
    _record_session_file().write_text(json.dumps(job, indent=2), encoding="utf-8")


def load_record() -> dict[str, Any] | None:
    p = _record_session_file()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def clear_record() -> None:
    p = _record_session_file()
    if p.exists():
        try: p.unlink()
        except OSError: pass


def record_alive() -> bool:
    """Whether the active record subprocess is still running. Self-heals
    across satellite restarts via PID probe."""
    import os
    job = load_record()
    if not job:
        return False
    pid = int(job.get("pid", 0))
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# ─────────────────────────── model meta ───────────────────────────────────

def list_models() -> list[dict]:
    """Enumerate model metas in settings.models_dir."""
    out: list[dict] = []
    if not settings.models_dir.exists():
        return out
    for p in sorted(settings.models_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text("utf-8")))
        except Exception:
            continue
    return out


def load_model(slug: str) -> dict | None:
    p = settings.models_dir / f"{slug}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def save_model(meta: dict) -> None:
    slug = meta.get("slug")
    if not slug:
        raise ValueError("meta missing slug")
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    (settings.models_dir / f"{slug}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def delete_model(slug: str) -> None:
    p = settings.models_dir / f"{slug}.json"
    if p.exists():
        p.unlink()


def record_metrics(slug: str, metrics: dict[str, float], *, history_row: dict | None = None) -> None:
    """Called by trainer.py on natural training completion.

    Two writes:
      1. `meta["metrics"]` — the latest "for-glance" snapshot
         (acc/recall/fp + trained_at). Powers the ModelsCard chips.
      2. `meta["training_history"]` — append `history_row` (or fall
         back to the bare metrics if caller didn't pass one). Capped
         at the last 25 runs so deep iteration loops don't bloat the
         JSON unboundedly.
    """
    meta = load_model(slug) or {"slug": slug, "created_at": time.time()}
    glance = {**metrics, "trained_at": time.time()}
    meta["metrics"] = glance
    history = meta.get("training_history") or []
    history.append(history_row or glance)
    meta["training_history"] = history[-25:]
    save_model(meta)
