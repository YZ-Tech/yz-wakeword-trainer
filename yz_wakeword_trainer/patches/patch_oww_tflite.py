"""Comment out OWW's unconditional onnx→tflite conversion call.

OWW's train.py:901 calls convert_onnx_to_tflite() AFTER successfully saving
the .onnx — which is the only artifact JarvYZ uses. The conversion requires
`onnx_tf` (not installed by design — adds tensorflow as a dep). Without the
patch, every successful training ends with a scary traceback + non-zero exit
code that wrappers misread as failure.

Identical logic to the existing _patch_oww_tflite.py at
y:/projects/assistant/tools/wakeword/_patch_oww_tflite.py — moved here so
the satellite owns it.

Reversible: replace SKIPPED-BY-JARVIS-TFLITE markers with the original lines.
Won't survive `pip install --upgrade openwakeword`; reapply via apply() each run.
"""
from __future__ import annotations

from pathlib import Path


def _find_oww_train_py() -> Path | None:
    """Locate openwakeword/train.py WITHOUT executing it.

    `import openwakeword.train` would add it to sys.modules, then
    `runpy.run_module('openwakeword.train', '__main__')` in the
    launcher would see it as already-imported and SKIP re-execution —
    the `if __name__ == '__main__'` block (argparse + actual training)
    would silently not run.

    `find_spec` resolves the spec without executing the module body.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("openwakeword.train")
        if spec and spec.origin:
            return Path(spec.origin)
    except Exception:
        pass
    return None


NEEDLE = "        # Convert the model from onnx to tflite format\n        convert_onnx_to_tflite("
REPLACEMENT = (
    "        # SKIPPED-BY-JARVIS-TFLITE: Convert the model from onnx to tflite format\n"
    "        # SKIPPED-BY-JARVIS-TFLITE: convert_onnx_to_tflite("
)


def apply() -> str:
    train_py = _find_oww_train_py()
    if train_py is None:
        return "patch_oww_tflite: SKIPPED (openwakeword not installed?)"
    src = train_py.read_text(encoding="utf-8")
    if REPLACEMENT in src:
        return "patch_oww_tflite: already applied"
    if NEEDLE not in src:
        return "patch_oww_tflite: SKIPPED (needle not found — OWW upstream may have changed)"
    new_src = src.replace(NEEDLE, REPLACEMENT, 1)
    # Continuation line (the second arg + closing paren) — also comment out.
    marker = REPLACEMENT.splitlines()[-1]
    idx = new_src.find(marker)
    line_end = new_src.find("\n", idx) + 1
    next_end = new_src.find("\n", line_end) + 1
    cont = new_src[line_end:next_end]
    if "convert_onnx_to_tflite" not in cont and ".tflite" in cont:
        new_src = new_src[:line_end] + "        # SKIPPED-BY-JARVIS-TFLITE: " + cont.lstrip() + new_src[next_end:]
    train_py.write_text(new_src, encoding="utf-8")
    return f"patch_oww_tflite: patched {train_py}"
