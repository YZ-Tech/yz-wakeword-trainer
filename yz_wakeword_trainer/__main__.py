"""`python -m yz_wakeword_trainer` → boot the HTTP server.

Reads HOST and PORT from settings (env-overridable: JWT_HOST, JWT_PORT). Defaults
to 127.0.0.1:9001 — localhost-only by design, set JWT_HOST=0.0.0.0 to expose to LAN.
"""
from __future__ import annotations

# Must run BEFORE `import onnxruntime` or `import openwakeword`. Prepends
# the nvidia wheels' DLL dirs to PATH so `onnxruntime-gpu` can find
# cuDNN at CUDAExecutionProvider init time.
from . import _cuda_dll_path as _cuda
print(_cuda.configure(), flush=True)

# Apply persistent overrides from <wakeword_root>/settings.json before any
# request hits the server. Layers over the env defaults; once the UI
# saves a setting the JSON wins forever.
from .core import persistent_settings as _persistent
print(_persistent.apply(), flush=True)

import uvicorn

from .settings import settings


def main() -> None:
    import os

    # YZ_PORT = the port core resolved (settings.ports override) — wins over
    # this satellite's own settings/env so the bind always matches the
    # client URL; JWT_PORT + persistent settings serve standalone runs.
    uvicorn.run(
        "yz_wakeword_trainer.server:app",
        host=settings.host,
        port=int(os.environ.get("YZ_PORT") or settings.port),
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
