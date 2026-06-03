"""User-mutable settings persisted to <wakeword_root>/settings.json.

Why a side file instead of editing the pydantic defaults at runtime:
the pydantic `Settings` model is constructed once at module import with
defaults from env vars. To let the UI add/remove things like extra
background-audio paths without requiring shell env manipulation + a
satellite restart, we layer a JSON file over the env defaults:

    env var (JWT_*)  →  pydantic default_factory  →  JSON file (overrides)

The file is authoritative once it exists (any field present in the JSON
wins). On first run nothing is persisted; the env defaults stand. The
moment the UI saves a setting, the JSON file gets created and from
then on it's the source of truth.

Intentionally narrow: only mirrors the handful of fields the UI can
actually edit. Adding a new editable field = add it to MUTABLE_KEYS
plus a handler in apply()/dump().
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..settings import settings

log = logging.getLogger(__name__)

# Keys the UI is allowed to read/write. Anything else in the JSON is
# silently ignored on load (so a future remove-this-key cleanup doesn't
# crash an older satellite).
MUTABLE_KEYS = ("extra_background_paths", "global_negative_phrases")


def _settings_file() -> Path:
    return settings.wakeword_root / "settings.json"


def apply() -> str:
    """Load the persistent file (if present) and overwrite the matching
    fields on the in-memory `settings` instance. Returns a one-line
    status string for the boot log."""
    p = _settings_file()
    if not p.exists():
        return "persistent_settings: no file yet (using env/defaults)"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return f"persistent_settings: SKIPPED (parse error: {e})"

    applied: list[str] = []
    if "extra_background_paths" in data:
        settings.extra_background_paths = [
            Path(x) for x in (data["extra_background_paths"] or [])
        ]
        applied.append(f"extra_background_paths={len(settings.extra_background_paths)}")
    if "global_negative_phrases" in data:
        settings.global_negative_phrases = list(data["global_negative_phrases"] or [])
        applied.append(f"global_negative_phrases={len(settings.global_negative_phrases)}")

    return f"persistent_settings: loaded {p.name} ({', '.join(applied) or 'nothing applicable'})"


def dump() -> dict:
    """Return the current values for the mutable keys. Used by GET
    /settings (so the UI can read what's currently in effect)."""
    return {
        "extra_background_paths": [str(p) for p in settings.extra_background_paths],
        "global_negative_phrases": list(settings.global_negative_phrases),
    }


def save() -> Path:
    """Write the current MUTABLE_KEYS values to disk. Called after any
    successful PATCH so the next satellite restart picks them up."""
    p = _settings_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
