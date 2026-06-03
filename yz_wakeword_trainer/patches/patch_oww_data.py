"""Clamp `n_words` in OWW's adversarial-text sampler so short phrase lists
(e.g. "hey, loom" tokenized into 2 words) don't crash np.random.choice.

Identical to y:/projects/assistant/tools/wakeword/_patch_oww.py — moved
into the satellite's patches/.
"""
from __future__ import annotations

from pathlib import Path


def _find_oww_data_py() -> Path | None:
    """Locate openwakeword/data.py WITHOUT importing it.

    `import openwakeword.data` would add it to sys.modules, which then
    prevents runpy.run_module('openwakeword.train', '__main__') in the
    launcher from re-executing the module's `if __name__ == '__main__'`
    block — the actual training would silently not start.

    `find_spec` only resolves the spec; it does not execute the module.
    The parent `openwakeword` package IS imported as a side-effect
    (Python has to do that to locate submodules), but `openwakeword`'s
    __init__.py only imports model/vad/custom_verifier_model — NOT
    train or data — so we're safe.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("openwakeword.data")
        if spec and spec.origin:
            return Path(spec.origin)
    except Exception:
        pass
    return None


NEEDLE = 'adversarial_texts.append(" ".join(np.random.choice(txts, size=n_words, replace=False)))'
REPLACEMENT = 'adversarial_texts.append(" ".join(np.random.choice(txts, size=min(n_words, len(txts)), replace=False)))'


def apply() -> str:
    data_py = _find_oww_data_py()
    if data_py is None:
        return "patch_oww_data: SKIPPED (openwakeword not installed?)"
    src = data_py.read_text(encoding="utf-8")
    if REPLACEMENT in src:
        return "patch_oww_data: already applied"
    if NEEDLE not in src:
        return "patch_oww_data: SKIPPED (needle not found)"
    data_py.write_text(src.replace(NEEDLE, REPLACEMENT, 1), encoding="utf-8")
    return f"patch_oww_data: patched {data_py}"
