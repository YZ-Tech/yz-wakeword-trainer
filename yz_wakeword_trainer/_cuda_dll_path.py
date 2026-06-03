"""Prepend NVIDIA-wheel DLL dirs to PATH so `onnxruntime-gpu` finds cuDNN
+ cuBLAS at provider-init time.

`pip install nvidia-cudnn-cu12` drops DLLs at
`site-packages/nvidia/cudnn/bin/cudnn64_9.dll`. `os.add_dll_directory`
only affects Python-side LoadLibrary calls — onnxruntime's C++ provider
loader uses the bare Windows DLL search, which DOES walk PATH. So we
prepend the nvidia/*/bin dirs to PATH at process startup.

Without this, `CUDAExecutionProvider` silently falls back to
`CPUExecutionProvider` with a "Failed to create CUDAExecutionProvider"
warning, and OWW's training-time feature extraction runs on CPU.

Idempotent (checks for a sentinel marker in PATH).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_MARKER = "jarvis_cuda_dll_path_applied"


def configure() -> str:
    """Mutate os.environ["PATH"] in place. Returns a one-line status."""
    if _MARKER in os.environ.get("PATH", ""):
        return "cuda_dll_path: already configured"

    # Windows nvidia wheels install at `<venv>/Lib/site-packages/nvidia/`.
    # Linux puts them at `lib/python3.X/site-packages/nvidia/`, but Linux
    # also doesn't use PATH for shared-lib resolution (it uses LD_LIBRARY_PATH
    # + rpath which the wheels usually configure correctly) — so there's
    # nothing for THIS module to do. The log line should reflect that
    # difference; on Linux this is normal, not a failure.
    if os.name != "nt":
        return "cuda_dll_path: not needed on this OS (Linux uses LD_LIBRARY_PATH / rpath)"
    venv_lib = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not venv_lib.exists():
        return f"cuda_dll_path: SKIPPED (no nvidia/ dir at {venv_lib})"

    candidates = [
        venv_lib / "cudnn" / "bin",
        venv_lib / "cublas" / "bin",
        venv_lib / "cuda_runtime" / "bin",
        venv_lib / "cuda_nvrtc" / "bin",
    ]
    found = [p for p in candidates if p.exists()]
    if not found:
        return "cuda_dll_path: SKIPPED (no cudnn/cublas dirs under nvidia/)"

    sep = ";" if os.name == "nt" else ":"
    pieces = [str(p) for p in found]
    existing = os.environ.get("PATH", "")
    # Drop a sentinel env var so child subprocesses skip a redundant prepend.
    os.environ["JWT_" + _MARKER.upper()] = "1"
    os.environ["PATH"] = sep.join(pieces) + sep + existing + sep + _MARKER
    return f"cuda_dll_path: prepended {len(found)} NVIDIA DLL dir(s)"
