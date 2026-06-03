"""Force `num_workers=0` in OWW's training DataLoader on Windows.

OWW's train.py instantiates a DataLoader with `num_workers=n_cpus` and
the dataset object's `label_transforms` map contains `lambda` functions.
Linux's `fork()` multiprocessing inherits these for free; Windows uses
`spawn()` which pickles the dataset, and lambdas aren't picklable.
Result: `_pickle.PicklingError: Can't pickle <function <lambda>>`.

The single safe fix on Windows is `num_workers=0` — runs in the main
process, no pickling. Training is bound by CPU feature extraction up
to here anyway; the loss in parallelism for the actual training loop
is small.

Linux installs are untouched: `sys.platform != 'win32'` → no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _find_oww_train_py() -> Path | None:
    try:
        import importlib.util
        spec = importlib.util.find_spec("openwakeword.train")
        if spec and spec.origin:
            return Path(spec.origin)
    except Exception:
        pass
    return None


NEEDLE = "        X_train = torch.utils.data.DataLoader(IterDataset(batch_generator),\n                                              batch_size=None, num_workers=n_cpus, prefetch_factor=16)"
REPLACEMENT = (
    "        # Patched (Windows): num_workers=0 because DataLoader's spawn\n"
    "        # multiprocessing on Windows tries to pickle the dataset\n"
    "        # (including the lambda label_transforms), which fails. The\n"
    "        # cost is in-process iteration; data flow is unchanged.\n"
    "        import sys as _sys\n"
    "        _nw = 0 if _sys.platform == 'win32' else n_cpus\n"
    "        _pf = None if _nw == 0 else 16\n"
    "        X_train = torch.utils.data.DataLoader(IterDataset(batch_generator),\n"
    "                                              batch_size=None, num_workers=_nw, prefetch_factor=_pf)"
)


def apply() -> str:
    train_py = _find_oww_train_py()
    if train_py is None:
        return "patch_oww_dataloader_workers: SKIPPED (not installed)"
    src = train_py.read_text(encoding="utf-8")
    if "_nw = 0 if _sys.platform == 'win32'" in src:
        return "patch_oww_dataloader_workers: already applied"
    if NEEDLE not in src:
        return "patch_oww_dataloader_workers: SKIPPED (needle not found)"
    train_py.write_text(src.replace(NEEDLE, REPLACEMENT, 1), encoding="utf-8")
    return f"patch_oww_dataloader_workers: patched {train_py.name}"
