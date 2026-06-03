"""Stub `generate_samples` so OWW's unconditional module-level import
on Windows doesn't crash.

openwakeword/train.py at line 638-639 does:

    sys.path.insert(0, os.path.abspath(config["piper_sample_generator_path"]))
    from generate_samples import generate_samples

That import lives at module scope, BEFORE any args check — so it runs even
when the user only asks for `--train_model` (no clip generation). On
Windows the upstream `piper-sample-generator` package is unbuildable (it
depends on `piper-phonemize`), so the import explodes.

We don't NEED `generate_samples` at runtime in the train_model code path:
clip_gen.py (this satellite's native piper-tts replacement) populates the
positive/negative dirs before OWW launches, and the `if args.generate_clips
is True` branch in OWW gates the actual call site. Pre-inserting a stub
into `sys.modules` makes the bare import succeed; if the stubbed function
is ever called, it raises with a helpful message instead of producing
silent garbage.
"""
from __future__ import annotations


def apply() -> str:
    import sys
    import types

    if "generate_samples" in sys.modules and getattr(
        sys.modules["generate_samples"], "_jarvis_stub", False
    ):
        return "patch_oww_train_imports: already stubbed"

    mod = types.ModuleType("generate_samples")
    mod._jarvis_stub = True  # type: ignore[attr-defined]

    def generate_samples(*args, **kwargs):
        raise RuntimeError(
            "generate_samples() called on Windows. The satellite's clip_gen "
            "(piper-tts) is supposed to pre-populate the positive/negative "
            "dirs; OWW should hit its 'enough clips exist, skipping' branch "
            "and never reach this code path. If you see this, OWW thinks "
            "the bucket is empty — check the dataset dirs."
        )

    mod.generate_samples = generate_samples  # type: ignore[attr-defined]
    sys.modules["generate_samples"] = mod
    return "patch_oww_train_imports: stubbed generate_samples module"
