"""Subprocess entrypoint that applies patches in-process, then runs
openwakeword.train.

Why a launcher: the scipy `sph_harm` shim is a runtime monkey-patch — it
must happen IN THE SAME PROCESS as openwakeword's import. We can't patch
from the parent and have the child inherit (the child is a fresh interpreter).

Spawned by `core/trainer.py` as:
    [sys.executable, "-m", "yz_wakeword_trainer._launcher",
     "--training_config", PATH, "--train_model"]

The two file-on-disk patches (patch_oww_data, patch_oww_tflite) are also
applied here — they're cheap idempotent edits that survive across runs but
re-application is safe.
"""
from __future__ import annotations

import sys


def _ensure_oww_resources() -> str:
    """openWakeWord ships small ONNX files
    (melspectrogram.onnx, embedding_model.onnx, silero_vad.onnx) under
    `<oww>/resources/models/`, but `pip install` does NOT auto-fetch
    them — they're downloaded on first call to `download_models()`.

    Without them, `AudioFeatures.__init__` crashes with
    `onnxruntime.capi.onnxruntime_pybind11_state.NoSuchFile`. This bit
    the first-run WSL test. Bootstrap them here so any clean install
    on any OS Just Works on the first /train.

    Idempotent: if the required files are already present, no-op (no
    network call). One-shot ~50 MB download otherwise.
    """
    try:
        import openwakeword
        from pathlib import Path as _P
    except ImportError:
        return "oww_resources: SKIPPED (openwakeword not importable)"
    res_dir = _P(openwakeword.__file__).parent / "resources" / "models"
    needed = ("melspectrogram.onnx", "embedding_model.onnx")
    missing = [n for n in needed if not (res_dir / n).exists()]
    if not missing:
        return f"oww_resources: present at {res_dir}"
    try:
        import openwakeword.utils
        openwakeword.utils.download_models()
    except Exception as e:  # noqa: BLE001
        return f"oww_resources: FAILED to download ({type(e).__name__}: {e})"
    return f"oww_resources: downloaded ({', '.join(missing)})"


def _install_metrics_log_handler() -> str:
    """Add a clean python-logging FileHandler that captures OWW's
    `Final Model …` lines reliably.

    Background: OWW emits its final accuracy/recall/FP metrics via
    `logging.info("Final Model Accuracy: …")`. Those go through the
    root logger, which by default writes to stderr — shared with tqdm
    on Windows. tqdm's frequent stderr writes + buffering cause the
    final logging lines to be lost between the last tqdm flush and the
    subprocess exit. Result: training succeeds, .onnx lands, but the
    log file has zero metrics → reaper has nothing to parse.

    The fix is a dedicated FileHandler pointing at
    `<output_dir>/<slug>/training_metrics.log`. Logging goes there
    DIRECTLY, bypasses tqdm's stderr contention, gets flushed on
    handler close. The training log still gets the full tqdm noise
    plus the metrics (we don't replace, we add).
    """
    import logging
    import yaml
    from pathlib import Path

    # Pull --training_config out of argv to find slug + output_dir.
    cfg_path = None
    for i, a in enumerate(sys.argv):
        if a == "--training_config" and i + 1 < len(sys.argv):
            cfg_path = Path(sys.argv[i + 1])
            break
    if cfg_path is None or not cfg_path.exists():
        return "metrics_log: SKIPPED (no --training_config in argv)"

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    slug = cfg.get("model_name")
    output_dir = cfg.get("output_dir")
    if not slug or not output_dir:
        return "metrics_log: SKIPPED (config missing model_name or output_dir)"

    # output_dir is cwd-relative; cwd is wakeword_root. Make absolute.
    target = Path(output_dir) / slug / "training_metrics.log"
    if not target.is_absolute():
        target = Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Truncate so the parsed file only reflects THIS run.
    target.write_text("", encoding="utf-8")

    fh = logging.FileHandler(target, mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)  # default WARNING swallows info
    return f"metrics_log: → {target}"


def main() -> None:
    # 0. Make sure NVIDIA DLL dirs are on PATH. The satellite parent
    # already does this in __main__.py before spawning us, but configuring
    # again here means the launcher works standalone too (and it's a
    # no-op if PATH already has the sentinel).
    from yz_wakeword_trainer import _cuda_dll_path as _cuda
    print(f"[launcher] {_cuda.configure()}", flush=True)

    # 1. Apply patches BEFORE any openwakeword/acoustics imports
    from yz_wakeword_trainer.patches import apply_all
    for line in apply_all():
        print(f"[launcher] {line}", flush=True)
    # 1b. Bootstrap OWW's bundled ONNX resources if a fresh install
    # didn't download them. One-time ~50 MB; idempotent.
    print(f"[launcher] {_ensure_oww_resources()}", flush=True)
    # 1c. Capture OWW's final metrics into a dedicated file so the reaper
    # can parse them later (tqdm-on-stderr clobbers the noisy log).
    print(f"[launcher] {_install_metrics_log_handler()}", flush=True)
    print("[launcher] patches applied — invoking openwakeword.train", flush=True)
    print("---", flush=True)

    # 2. Defensive: if anything (patches, dependency import) snuck
    #    openwakeword.train OR openwakeword.data into sys.modules, runpy
    #    will skip re-executing the module body and the `if __name__ ==
    #    "__main__"` block (argparse + training) never fires. Pop them.
    for mod in ("openwakeword.train", "openwakeword.data"):
        sys.modules.pop(mod, None)

    # 3. Hand off to openwakeword.train as if it were called via `python -m`.
    #    argv[0] becomes 'openwakeword.train' so argparse sees the right name.
    import runpy
    sys.argv = ["openwakeword.train"] + sys.argv[1:]
    runpy.run_module("openwakeword.train", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
