"""Expand torch-audiomentations' allowed background-audio extensions.

Upstream hardcodes `SUPPORTED_EXTENSIONS = (".wav",)` in
`torch_audiomentations/utils/file.py`, which means OWW's
`AddBackgroundNoise` finds zero files when pointed at:
- LibriSpeech (.flac)
- fma_small  (.mp3)
- Common Voice (.mp3)

In-process monkey-patch of both the module-level constant AND the
function defaults so `find_audio_files_in_paths()` accepts mp3/flac/ogg/
opus/m4a. Backed by torchaudio's actual decoder support — these formats
all play through `torchaudio.load()` once `soundfile` or `ffmpeg` is
present (both are in the standard `audiomentations[all]` install).

Idempotent: a sentinel attribute on the module marks the patch applied.
"""
from __future__ import annotations

_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a")


def apply() -> str:
    try:
        from torch_audiomentations.utils import file as _f
    except ImportError:
        return "patch_torch_audiomentations_exts: SKIPPED (torch_audiomentations not installed)"
    if getattr(_f, "_jarvis_patched_exts", False):
        return "patch_torch_audiomentations_exts: already patched"

    _f.SUPPORTED_EXTENSIONS = _EXTS
    # The function captures the default at def-time; rebind it so callers
    # that don't pass filename_endings explicitly pick up the new tuple.
    fn = _f.find_audio_files_in_paths
    if fn.__defaults__:
        new_defaults = tuple(
            _EXTS if (isinstance(d, tuple) and all(isinstance(x, str) and x.startswith(".") for x in d)) else d
            for d in fn.__defaults__
        )
        fn.__defaults__ = new_defaults

    # AddBackgroundNoise (and other consumers) imported find_audio_files_in_paths
    # by name at module import time, so the bound reference inside their
    # modules is the SAME function object. Mutating its __defaults__ above is
    # enough — no need to rebind in each consumer.

    _f._jarvis_patched_exts = True  # type: ignore[attr-defined]
    return f"patch_torch_audiomentations_exts: extensions = {_EXTS}"
