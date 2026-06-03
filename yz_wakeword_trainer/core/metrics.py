"""Parse OWW's `Final Model …` block out of a training log.

Returns {accuracy, recall, fp_per_hour} as floats. Returns None if any of
the three is missing — that's the signal that training didn't reach the
metrics phase (user stopped early, or trainer crashed earlier).
"""
from __future__ import annotations

import re


_PATTERNS = {
    "accuracy":    re.compile(r"Final Model Accuracy:\s+([\d.]+)"),
    "recall":      re.compile(r"Final Model Recall:\s+([\d.]+)"),
    "fp_per_hour": re.compile(r"Final Model False Positives per Hour:\s+([\d.]+)"),
}


def parse(log_text: str) -> dict[str, float] | None:
    out: dict[str, float] = {}
    for key, pat in _PATTERNS.items():
        m = pat.search(log_text)
        if not m:
            return None
        try:
            out[key] = float(m.group(1))
        except ValueError:
            return None
    return out


def parse_file(path) -> dict[str, float] | None:
    """Read a file and parse via `parse`. Returns None if missing/empty/
    incomplete. Used by the reaper to prefer the dedicated metrics log
    over the noisy tqdm-trampled training log."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    try:
        return parse(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
