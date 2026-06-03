"""Close memmap handles before file ops in OWW's feature pipeline.

TWO Linux-only bugs in OWW that bite Windows:

1. `utils.compute_features_from_generator` creates `fp = open_memmap(...,
   mode='w+', ...)` then calls `trim_mmap(output_file)` while `fp` is
   STILL HOLDING the file open. On Linux unlink-while-open is fine;
   on Windows it raises `PermissionError: [WinError 32]` inside trim_mmap.

2. `data.trim_mmap` opens its own read mmap + creates a w+ mmap of the
   trimmed copy, then `os.remove(mmap_path)` the original. Same Windows
   issue — both inner mmaps need explicit closes too.

Fix is a disk patch on BOTH files:
- `utils.py`: insert `del fp; gc.collect()` just before `trim_mmap(...)`.
- `data.py`: explicitly close the `_mmap` buffer for both inner mmaps
  before the os.remove + os.rename.
"""
from __future__ import annotations

from pathlib import Path


def _find_oww_data_py() -> Path | None:
    try:
        import importlib.util
        spec = importlib.util.find_spec("openwakeword.data")
        if spec and spec.origin:
            return Path(spec.origin)
    except Exception:
        pass
    return None


def _find_oww_utils_py() -> Path | None:
    try:
        import importlib.util
        spec = importlib.util.find_spec("openwakeword.utils")
        if spec and spec.origin:
            return Path(spec.origin)
    except Exception:
        pass
    return None


UTILS_NEEDLE = "    # Trip empty rows from the mmapped array\n    trim_mmap(output_file)"
UTILS_REPLACEMENT = (
    "    # Patched (Windows-safe):\n"
    "    # 1. Release `fp`'s write-mode mmap so the file handle is dropped\n"
    "    #    (Windows can't os.remove or os.rename a mmap-locked file).\n"
    "    # 2. Skip trim_mmap entirely if we filled the array exactly\n"
    "    #    (row_counter == n_total) — no empty rows to trim, and the\n"
    "    #    function's del-then-rename dance is what triggers the lock.\n"
    "    _m = getattr(fp, '_mmap', None)\n"
    "    if _m is not None:\n"
    "        try: _m.close()\n"
    "        except Exception: pass\n"
    "    del fp\n"
    "    import gc as _gc\n"
    "    _gc.collect()\n"
    "    if row_counter < n_total:\n"
    "        # Trip empty rows from the mmapped array\n"
    "        trim_mmap(output_file)"
)


# Anchor on the remove line — the lines just above it are mmap2.flush etc.
NEEDLE = "    # Remove old mmaped file\n    os.remove(mmap_path)"

# Note `mmap_file2._mmap.close()` — without this, mmap_file2 still holds
# a write-mode handle to output_file2 (the .npy2 file) and the subsequent
# os.rename(output_file2, mmap_path) also fails on Windows.
REPLACEMENT = (
    "    # Patched: explicitly close the memmap buffers before file ops.\n"
    "    # On Windows, open mmaps lock the file; `del` alone leaves the\n"
    "    # underlying buffer alive long enough that os.remove + os.rename\n"
    "    # raise PermissionError. numpy.memmap exposes `_mmap` for this.\n"
    "    for _arr in (mmap_file1, mmap_file2):\n"
    "        _m = getattr(_arr, '_mmap', None)\n"
    "        if _m is not None:\n"
    "            try: _m.close()\n"
    "            except Exception: pass\n"
    "    del mmap_file1\n"
    "    del mmap_file2\n"
    "    import gc as _gc\n"
    "    _gc.collect()\n"
    "    # Remove old mmaped file\n"
    "    os.remove(mmap_path)"
)


def apply() -> str:
    results: list[str] = []

    # ── data.py: close inner mmaps in trim_mmap ──
    data_py = _find_oww_data_py()
    if data_py is None:
        results.append("data.py SKIPPED (not installed)")
    else:
        src = data_py.read_text(encoding="utf-8")
        if "_arr in (mmap_file1, mmap_file2)" in src:
            results.append("data.py already")
        else:
            prior = "    # Patched: close memmap handles before removing"
            if prior in src:
                prior_block_start = src.find(prior)
                prior_block_end = src.find("    # Remove old mmaped file", prior_block_start)
                src = src[:prior_block_start] + src[prior_block_end:]
            if NEEDLE not in src:
                results.append("data.py SKIPPED (needle missing)")
            else:
                data_py.write_text(src.replace(NEEDLE, REPLACEMENT, 1), encoding="utf-8")
                results.append("data.py patched")

    # ── utils.py: close outer `fp` before trim_mmap + skip if no empty rows ──
    utils_py = _find_oww_utils_py()
    if utils_py is None:
        results.append("utils.py SKIPPED (not installed)")
    else:
        src = utils_py.read_text(encoding="utf-8")
        if "if row_counter < n_total:" in src:
            results.append("utils.py already")
        else:
            # Revert any prior weaker patch so we re-apply cleanly.
            prior_marker = "    # Patched: release `fp`'s write-mode mmap"
            prior_alt = "    # Patched (Windows-safe):"
            for m in (prior_marker, prior_alt):
                if m in src:
                    start = src.find(m)
                    end = src.find("    # Trip empty rows from the mmapped array", start)
                    if end > start:
                        src = src[:start] + src[end:]
            if UTILS_NEEDLE not in src:
                results.append("utils.py SKIPPED (needle missing)")
            else:
                utils_py.write_text(src.replace(UTILS_NEEDLE, UTILS_REPLACEMENT, 1), encoding="utf-8")
                results.append("utils.py patched")

    return "patch_oww_trim_mmap: " + ", ".join(results)
