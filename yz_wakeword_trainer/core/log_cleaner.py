"""Trainer-log → user-readable transform.

Stateful pass over the raw log: drops separator banners, deprecation/dtype
warnings, post-success tflite tracebacks; rewrites recognizable milestones
with ▶/✓ markers; collapses tqdm progress to one row per section.

Pure function (no I/O). Called per /jobs/{id}/log request. Cheap enough on
the typical ~5-10 KB tail payload that no caching is needed.

Ported verbatim from web/api/wakeword_dev.py — this is its new home.
"""
from __future__ import annotations

import re


_NOISY_TRAIN_LINE = re.compile(r"DEBUG:generate_samples:Batch \d+/\d+ complete")

# tqdm progress lines:
#   "Computing features:  43%|████▎     | 670/1562 [01:04<01:19, 11.17it/s]"
#   "Training:  78%|███████▊  | 38751/50000 [23:31<02:25, 77.35it/s]"
_TQDM_PROGRESS_LINE = re.compile(r"^([A-Za-z][\w ]+):\s+(\d+)%\|")
_TQDM_IT_LINE = re.compile(r"^([A-Za-z][\w ]+):\s+\d+it\s*\[")

# Pure-banner line (########################################).
_BANNER_RE = re.compile(r"^#{10,}\s*$")

# OWW emits `INFO:root:` / `WARNING:root:` prefixes — strip them in display.
_PREFIX_RE = re.compile(r"^(INFO|WARNING|DEBUG|ERROR):root:")

# Tflite-epilogue markers. With patch_oww_tflite applied the trainer no
# longer reaches this point, but keep filter as a safety net for unpatched
# OWW installs.
_TFLITE_NOISE_MARKERS = (
    "convert_onnx_to_tflite",
    "from onnx_tf",
    "No module named 'onnx_tf'",
    "subprocess.CalledProcessError: Command '[",  # OWW wrapper failure trace
)

# Milestone rewrites — turn raw OWW log lines into ▶/✓-marked sections.
_MILESTONE_REWRITES: tuple[tuple[re.Pattern[str], object], ...] = (
    (re.compile(r"^Generating (positive|negative) clips for (training|testing)$"),
     r"▶ \1 clips · \2"),
    # OWW typo: "clips testing" missing "for" in some releases. Tolerate both.
    (re.compile(r"^Skipping generation of (positive|negative) clips (?:for )?(training|testing), as ~(\d+) already exist"),
     r"  \1 clips · \2: \3 cached (skipping)"),
    (re.compile(r"^Computing openwakeword features for generated samples$"),
     "▶ Computing features"),
    (re.compile(r"^Starting training sequence (\d+)\.\.\.$"),
     r"▶ Training sequence \1/3"),
    (re.compile(r"^Increasing weight on negative examples to reduce false positives\.\.\.$"),
     "  (boosting negative weight)"),
    (re.compile(r"^Merging checkpoints above the 90th percentile into single model\.\.\.$"),
     "▶ Merging top-decile checkpoints"),
    (re.compile(r"^Final Model Accuracy:\s+([\d.]+)$"),
     lambda m: f"✓ Accuracy:  {float(m.group(1)) * 100:6.2f}%"),
    (re.compile(r"^Final Model Recall:\s+([\d.]+)$"),
     lambda m: f"✓ Recall:    {float(m.group(1)) * 100:6.2f}%"),
    (re.compile(r"^Final Model False Positives per Hour:\s+([\d.]+)$"),
     lambda m: f"✓ FP/hour:   {float(m.group(1)):6.1f}"),
    (re.compile(r"^Saving ONNX mode as '(.+)'$"),
     r"✓ Saved → \1"),
)


def _is_exception_line(s: str) -> bool:
    """A bare 'ModuleNotFoundError: ...' or 'CalledProcessError: ...' line
    — first word ends with Error/Exception, contains a colon."""
    if not s or not s[0].isupper():
        return False
    head = s.split(":", 1)[0]
    return ":" in s and ("Error" in head or "Exception" in head)


def clean(raw: str) -> str:
    """Transform the raw trainer log into a user-friendly view."""
    out: list[str] = []
    skip_until_blank = False
    in_traceback = False

    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        # Tflite epilogue: hard cutoff
        if any(m in stripped for m in _TFLITE_NOISE_MARKERS):
            break

        # Tracebacks — skip frames + indented lines until the bare
        # exception summary, then continue.
        if stripped == "Traceback (most recent call last):":
            in_traceback = True
            i += 1
            continue
        if in_traceback:
            if stripped.startswith("  ") or stripped == "" or _is_exception_line(stripped):
                if _is_exception_line(stripped):
                    in_traceback = False
                i += 1
                continue
            in_traceback = False

        # FutureWarning / UserWarning blocks — skip until the trailing
        # "warnings.warn(" line.
        if ":" in stripped and ("FutureWarning:" in stripped or "UserWarning:" in stripped):
            skip_until_blank = True
            i += 1
            continue
        if skip_until_blank:
            if "warnings.warn(" in stripped:
                skip_until_blank = False
            i += 1
            continue

        # Banner / orphan warnings.warn / known noise
        if _BANNER_RE.match(stripped):
            i += 1
            continue
        if stripped.strip() == "warnings.warn(":
            i += 1
            continue

        # Strip OWW's log prefixes from real content lines
        m_prefix = _PREFIX_RE.match(stripped)
        if m_prefix:
            stripped = stripped[m_prefix.end():]

        # Skip prefix-only blank lines + collapse runs of blanks
        if stripped == "":
            if out and out[-1] == "":
                i += 1
                continue
            out.append("")
            i += 1
            continue

        # "####" noise between Final-Model header lines
        if stripped.startswith("####") and stripped.strip("#") == "":
            i += 1
            continue

        # Milestone rewrites
        rewritten = stripped
        for pat, repl in _MILESTONE_REWRITES:
            m = pat.match(stripped)
            if m:
                rewritten = repl(m) if callable(repl) else pat.sub(repl, stripped)
                break

        out.append(rewritten)
        i += 1

    # Per-section tqdm dedup: keep only the latest line per label between
    # ▶-marked section boundaries.
    final: list[str] = []
    last_idx_by_label: dict[str, int] = {}
    for ln in out:
        if ln.startswith("▶ "):
            last_idx_by_label.clear()
            final.append(ln)
            continue
        m_pct = _TQDM_PROGRESS_LINE.match(ln)
        m_it = _TQDM_IT_LINE.match(ln)
        if m_pct or m_it:
            label = (m_pct or m_it).group(1)
            prior = last_idx_by_label.get(label)
            if prior is not None:
                final[prior] = ln
            else:
                last_idx_by_label[label] = len(final)
                final.append(ln)
            continue
        final.append(ln)

    # Squeeze stranded blanks the dedup may have left behind
    squeezed: list[str] = []
    for ln in final:
        if ln == "" and squeezed and squeezed[-1] == "":
            continue
        squeezed.append(ln)
    while squeezed and squeezed[0] == "":
        squeezed.pop(0)
    while squeezed and squeezed[-1] == "":
        squeezed.pop()
    return "\n".join(squeezed) + ("\n" if squeezed else "")
