"""Force AddBackgroundNoise.random_background to return exactly target_num_samples.

UPSTREAM BUG (torch_audiomentations 0.11.x):
`AddBackgroundNoise.random_background()` accumulates pieces from short
background files until it (thinks it) has enough samples to cover the
target. The loop tracks remaining budget like this:

    missing_num_samples -= background_num_samples     # estimate, from metadata
    background_samples = audio(background_path)        # actual loaded+resampled

The subtraction uses the *metadata-based estimate* (computed from
duration × target_sr), but the bucket grows by the *actual resampled
sample count*. Floating-point rounding in the resampler makes the
actual count typically 1 sample less than the estimate. After N small
files concatenated, the drift accumulates → final tensor has fewer
than target_num_samples → `apply_transform` crashes:

    RuntimeError: shape '[1, 1, 32000]' is invalid for input of size 31910

(31910 was observed with 90-sample drift across the loop's iterations.)

FIX: wrap random_background() to pad-or-trim its return to exactly
target_num_samples. Padding is silence (zeros), trimming is from the end.
A 90-sample mismatch out of 32000 (= 5.6 ms out of 2 s) is inaudible in
training augmentation; this is a strictly cosmetic length guarantee.

Idempotent via a sentinel attribute on the class.
"""
from __future__ import annotations


def apply() -> str:
    try:
        from torch_audiomentations.augmentations.background_noise import AddBackgroundNoise
    except ImportError:
        return "patch_torch_audiomentations_bg_length: SKIPPED (not installed)"
    if getattr(AddBackgroundNoise, "_jarvis_patched_bg_length", False):
        return "patch_torch_audiomentations_bg_length: already patched"

    import torch
    import torch.nn.functional as F

    _original = AddBackgroundNoise.random_background

    def _patched(self, audio, target_num_samples):  # type: ignore[no-redef]
        out = _original(self, audio, target_num_samples)
        cur = out.shape[-1]
        if cur == target_num_samples:
            return out
        if cur < target_num_samples:
            return F.pad(out, (0, target_num_samples - cur))
        # cur > target_num_samples — trim from end
        return out[..., :target_num_samples]

    AddBackgroundNoise.random_background = _patched
    AddBackgroundNoise._jarvis_patched_bg_length = True  # type: ignore[attr-defined]
    return "patch_torch_audiomentations_bg_length: random_background now length-clamped"
