"""Idempotent shims for upstream issues we don't control.

`apply_all()` is called by trainer.start() before spawning OWW. Each patch
is safe to call repeatedly — internal `if already_patched: return` guards.
"""
from __future__ import annotations

from . import (
    patch_scipy_acoustics,
    patch_oww_data,
    patch_oww_tflite,
    patch_oww_train_imports,
    patch_torch_audiomentations_exts,
    patch_torch_audiomentations_bg_length,
    patch_oww_trim_mmap,
    patch_oww_dataloader_workers,
)


def apply_all() -> list[str]:
    """Apply every patch. Returns list of one-line status strings (for the
    trainer banner). Each patch is best-effort — failure logs a warning but
    doesn't block training."""
    results: list[str] = []
    for mod in (
        patch_scipy_acoustics,
        patch_oww_data,
        patch_oww_tflite,
        patch_oww_train_imports,
        patch_torch_audiomentations_exts,
        patch_torch_audiomentations_bg_length,
        patch_oww_trim_mmap,
        patch_oww_dataloader_workers,
    ):
        try:
            results.append(mod.apply())
        except Exception as e:  # noqa: BLE001
            results.append(f"{mod.__name__}: SKIPPED ({e})")
    return results
