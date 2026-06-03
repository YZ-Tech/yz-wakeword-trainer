"""Satellite-local settings — does NOT import JarvYZ. Env-overridable with the
JWT_ prefix (wakeword-trainer)."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


def _env(name: str, default: str) -> str:
    return os.environ.get(f"JWT_{name}", default)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(f"JWT_{name}")
    return Path(raw) if raw else default


def _jarvyz_home() -> Path:
    """The shared data root — JARVYZ_HOME if set (core + every satellite read
    the same var), else ~/.jarvyz."""
    return Path(os.environ.get("JARVYZ_HOME") or Path.home() / ".jarvyz")


# One unified root for everything the trainer writes. Subdirs below are
# anchored here, AND the OWW subprocess cwd is set to this root so the
# template's `runs/...` and `corpora/...` paths resolve cleanly without
# any per-user string substitution.
#
# Lives under ~/.jarvyz/satellites/yz-wakeword-trainer/ — matches the source
# folder name. Signals "this is the trainer satellite's data, not
# JarvYZ-runtime data." JarvYZ's own app-data (settings.json, transcript/,
# openwakeword/ deploy target) lives directly under ~/.jarvyz/.
_WAKEWORD_ROOT = _env_path(
    "WAKEWORD_ROOT",
    _jarvyz_home() / "satellites" / "yz-wakeword-trainer",
)


class Settings(BaseModel):
    """Runtime configuration. Override with JWT_* env vars."""

    host: str = Field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(_env("PORT", "9001")))
    log_level: str = Field(default_factory=lambda: _env("LOG_LEVEL", "info"))

    # The unified satellite working tree. Everything below derives from
    # this. Layout:
    #   <wakeword_root>/runs/<slug>/{positive_train,*.npy,*.onnx}
    #   <wakeword_root>/corpora/{rirs,backgrounds}/...
    #   <wakeword_root>/.runtime_config.yaml  (rendered per-job)
    #   <wakeword_root>/train.log              (subprocess stdout/stderr)
    #   <wakeword_root>/train_session.json     (active-job marker)
    wakeword_root: Path = Field(default_factory=lambda: _WAKEWORD_ROOT)

    runs_dir: Path = Field(
        default_factory=lambda: _env_path("RUNS_DIR", _WAKEWORD_ROOT / "runs"),
        description="Per-slug subdirectories with positive/negative/test clips + features",
    )
    config_template: Path = Field(
        default_factory=lambda: _env_path(
            "CONFIG_TEMPLATE",
            Path(__file__).resolve().parents[3] / "backend" / "tools" / "wakeword" / "training_config.yaml",
        ),
        description="YAML template — relative paths anchored at wakeword_root cwd; render only substitutes slug + phrase lists",
    )
    runtime_config: Path = Field(
        default_factory=lambda: _env_path(
            "RUNTIME_CONFIG",
            _WAKEWORD_ROOT / ".runtime_config.yaml",
        ),
        description="Rendered per-run config that openwakeword.train consumes",
    )
    log_path: Path = Field(
        default_factory=lambda: _env_path("LOG_PATH", _WAKEWORD_ROOT / "train.log"),
        description="Where the trainer writes stdout/stderr. UI tails this.",
    )
    corpora_dir: Path = Field(
        default_factory=lambda: _env_path("CORPORA_DIR", _WAKEWORD_ROOT / "corpora"),
        description="RIR + background-audio corpora live here. Populated via /corpora/download.",
    )
    session_dir: Path = Field(
        default_factory=lambda: _env_path("SESSION_DIR", _WAKEWORD_ROOT),
        description="Per-job session JSON files live here (filename: train_session.json)",
    )

    # Per-model meta. JarvYZ reads the SAME files via its own
    # MODELS_META_DIR constant (kept in lockstep with the default below).
    models_dir: Path = Field(
        default_factory=lambda: _env_path("MODELS_DIR", _WAKEWORD_ROOT / "models"),
    )

    # Where downloaded piper-tts voice .onnx files cache. Used for native
    # clip generation when OWW's training_config doesn't already have
    # cached WAVs. Shared across slugs (voices are slug-agnostic).
    voices_dir: Path = Field(
        default_factory=lambda: _env_path(
            "VOICES_DIR",
            _jarvyz_home() / "piper-voices",
        ),
    )

    # Voices to rotate through for clip generation. Diverse selection
    # of en_US / en_GB voices balances per-clip variety. Override via
    # JWT_PIPER_VOICES (comma-separated) to use a different set.
    piper_voices: list[str] = Field(
        default_factory=lambda: (
            os.environ.get("JWT_PIPER_VOICES", "").split(",")
            if os.environ.get("JWT_PIPER_VOICES")
            else [
                "en_US-lessac-medium",
                "en_US-amy-medium",
                "en_US-ryan-medium",
                "en_US-libritts_r-medium",
                "en_GB-alan-medium",
                "en_GB-cori-medium",
            ]
        ),
    )

    # Global custom_negative_phrases — UI-mutable list that gets rendered
    # into every per-job config as the default negatives block. Per-slug
    # `negatives` in the model meta override these per-job. Persisted via
    # persistent_settings (settings.json). Default empty — UI populates.
    global_negative_phrases: list[str] = Field(default_factory=list)

    # Extra background-audio dirs to append to the rendered training
    # config's `background_paths` block. Useful for pointing at corpora
    # that live OUTSIDE the unified wakeword_root (e.g. a 35 GB Common
    # Voice download you already have in ~/Downloads/). Each entry is
    # checked for existence before being appended; missing dirs drop
    # silently same as the template-internal filter.
    #
    # Set via JWT_EXTRA_BACKGROUND_PATHS (os.pathsep-separated; `;` on
    # Windows, `:` on POSIX).
    extra_background_paths: list[Path] = Field(
        default_factory=lambda: [
            Path(p) for p in os.environ.get("JWT_EXTRA_BACKGROUND_PATHS", "").split(os.pathsep)
            if p.strip()
        ],
    )

    # Clip-count overrides for testing — when set, takes precedence over
    # `n_samples` / `n_samples_val` in the rendered training config. Lets
    # us run end-to-end smoke tests in minutes instead of hours without
    # editing the YAML by hand.
    clip_count_positive_train: int = Field(
        default_factory=lambda: int(os.environ.get("JWT_CLIPS_POS_TRAIN", "0"))
    )
    clip_count_positive_test: int = Field(
        default_factory=lambda: int(os.environ.get("JWT_CLIPS_POS_TEST", "0"))
    )
    clip_count_negative_train: int = Field(
        default_factory=lambda: int(os.environ.get("JWT_CLIPS_NEG_TRAIN", "0"))
    )
    clip_count_negative_test: int = Field(
        default_factory=lambda: int(os.environ.get("JWT_CLIPS_NEG_TEST", "0"))
    )

    # Hard kill-switch for the smart-invalidation wipe. Default OFF —
    # `_invalidate_stale_negative_data` returns 0 without touching disk
    # unless this is explicitly True, even when /train is called with
    # `wipe_stale=true`. Belt-and-suspenders on top of the API-level
    # gate: the user has been burned by silent re-wipes during failed
    # retry loops, so deletion requires an out-of-band opt-in.
    #
    # ENV-VAR ONLY — there is intentionally NO API/HTTP route to flip
    # this. The user sets `JWT_WIPE_ENABLED=1` in the environment before
    # starting the satellite when they truly want to allow a wipe; any
    # other path (UI, adapter, dialog bug) cannot toggle it. This is the
    # last-resort safety net.
    wipe_enabled: bool = Field(
        default_factory=lambda: os.environ.get("JWT_WIPE_ENABLED", "0") == "1"
    )


settings = Settings()
