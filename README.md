<!-- ─────────────────────────── JARVYZ SATELLITE ─────────────────────────── -->

# wakeword-trainer

[![JarvYZ](https://img.shields.io/badge/JARVYZ-Satellite-blue.svg?logoColor=white)](../../README.md)
[![Version](https://img.shields.io/badge/VERSION-0.1.0-blue.svg?logo=git&logoColor=white)](pyproject.toml)
[![Python](https://img.shields.io/badge/PYTHON-3.10–3.12-blue.svg?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/LICENSE-MIT-blue.svg?logo=opensourceinitiative&logoColor=white)](pyproject.toml)
[![Kind](https://img.shields.io/badge/KIND-service%20%2B%20CLI-blue.svg?logoColor=white)](#)
[![Port](https://img.shields.io/badge/PORT-9001-blue.svg?logoColor=white)](#)
[![Creator](https://img.shields.io/badge/CREATOR-Yeon-blue.svg?logo=github&logoColor=white)](https://github.com/YeonV)
[![Blade](https://img.shields.io/badge/A.K.A-Blade-darkred.svg?logo=github&logoColor=white)](https://github.com/YeonV)

<p align="left">
  <img src="ui/public/logo.svg" alt="JarvYZ" width="200">
</p>

> `yz-wakeword-trainer` — Standalone openWakeWord training service. Runs alone on a GPU box, optional JarvYZ integration.

### Techs

[![FastAPI](https://img.shields.io/badge/x-FastAPI-blue.svg?logo=fastapi&logoColor=white&label=)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/x-React-blue.svg?logo=react&logoColor=white&label=)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/x-TypeScript-blue.svg?logo=typescript&logoColor=white&label=)](https://www.typescriptlang.org/)
[![PyTorch](https://img.shields.io/badge/x-PyTorch-blue.svg?logo=pytorch&logoColor=white&label=)](https://pytorch.org/)

**Run** `python -m yz_wakeword_trainer` &nbsp;·&nbsp; **API** `/api/wakeword_dev/*`

<!-- ───────────────────────────────────────────────────────────────────────── -->

<details>
<summary><b>Documentation</b></summary>

Standalone HTTP service that trains openWakeWord models.

A **satellite** in the JarvYZ ecosystem — it has its own life outside JarvYZ. You can run
it on its own GPU box, point any number of clients at it (JarvYZ, a CLI, your own UI,
a Tampermonkey script), and it doesn't know or care who's calling. Same pattern as
Ollama for LLMs.

## Run standalone

```bash
pip install -e .
python -m yz_wakeword_trainer        # listens on http://127.0.0.1:9001
```

Or override port/host via env:

```bash
JWT_HOST=0.0.0.0 JWT_PORT=9001 python -m yz_wakeword_trainer
```

Browse to `http://127.0.0.1:9001/` — the satellite serves a self-contained
React UI (the same one JarvYZ embeds via dynamic-module). If you installed
from a built wheel, the UI is already bundled. From source: build it once:

```bash
cd ui
npm install
npm run build:pages   # outputs to ../yz_wakeword_trainer/static/
```

After that the satellite mounts the SPA at `/` (server.py:end checks if
`yz_wakeword_trainer/static/` is populated; mounts it if so, falls
through to API-only if not).

## UI build pipeline (for JarvYZ-embedded users)

The same UI also ships as an IIFE that JarvYZ loads via `@yz-dev/react-dynamic-module`:

```bash
cd ui
npm run ship          # = build:lib + copy IIFE to frontend/public/modules/
                      #   AND web/static/modules/ (JarvYZ serves both)
```

`build:lib` outputs `ui/dist-lib/yz-wakeword.iife.js`; the install step
copies it where JarvYZ's frontend can find it. Either build mode reads
the SAME source — only the entry point + bundle shape differ.

## Building a wheel (for distribution)

```bash
bash scripts/build_wheel.sh
```

That script does the right thing: installs UI deps if missing, builds
the SPA into `yz_wakeword_trainer/static/`, then runs `python -m build`.
Resulting wheel in `dist/` contains the SPA — `pip install` + run gives
a working UI at `http://127.0.0.1:9001/` with no further setup.

(Don't run `python -m build` directly — the SPA won't be built and the
wheel will be UI-less.)

## HTTP API

### Training jobs

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/train` | `{slug, config_overrides?, wipe_stale?}` | `{job_id}` |
| `GET` | `/train/preflight/{slug}` | — | `{would_wipe, cur_hash, last_hash, neg_*, npy_features, onnx_size}` |
| `GET` | `/jobs/{id}` | — | `{state, pgid, started_at, slug, metrics?}` |
| `GET` | `/jobs/{id}/log?tail=N&raw=bool` | — | `{log, lines}` |
| `WS` | `/jobs/{id}/log/stream` | — | newline-delimited log chunks |
| `POST` | `/jobs/{id}/stop` | — | `{ok, message}` |

`job_id` is currently always `"current"` (single concurrent training).

### Models

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/models` | — | per-slug meta list |
| `POST` | `/models` | `{phrase, slug?, language?}` | `{ok, slug}` — create new (no training started) |
| `POST` | `/models/{slug}/clone` | `{slug: new}` | `{ok, slug}` — shallow clone (fresh dataset) |
| `DELETE` | `/models/{slug}` | — | `{ok}` |
| `GET` | `/models/{slug}/onnx` | — | `FileResponse` — download trained ONNX |

### Negatives

| Method | Path | Body | Returns |
|---|---|---|---|
| `PUT` | `/negatives` | `{phrases: [str]}` | `{ok, count}` — global custom negatives |
| `PUT` | `/models/{slug}/negatives` | `{phrases: [str]}` | `{ok, count}` — per-model |

### Backgrounds (room recordings)

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/backgrounds` | — | list `[{name, size_bytes, mtime}]`, newest first |
| `DELETE` | `/backgrounds/{name}` | — | `{ok, deleted, name}` |

### Room ambient capture

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/record/start` | `{minutes, device?}` | `{ok, pid, minutes}` |
| `POST` | `/record/stop` | — | `{ok, stopped_pid?}` |
| `GET` | `/record/status` | — | `{running, pid, minutes, started_at}` |
| `GET` | `/record/log?tail=N` | — | `{log}` |

Captures 16 kHz mono PCM in 30 s chunks into `<wakeword_root>/corpora/backgrounds/loom_room/`.
Device picking: explicit name/index via `device`, else read from `~/.jarvyz/settings.json`'s
`audio.input_device`, else PortAudio default. Implementation in [core/record.py](yz_wakeword_trainer/core/record.py)
— absorbed from the legacy `tools/wakeword/record_room.py` so the satellite owns its own capture path.

### Corpora (downloads)

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/corpora/status` | — | `{corpora, ready}` |
| `POST` | `/corpora/download` | `{corpus}` (or `"all"`) | `{ok, corpora}` |
| `POST` | `/corpora/cancel` | `{corpus}` (or `"all"`) | `{ok}` |
| `WS` | `/corpora/progress` | — | server-pushed `{corpora}` snapshots |

### Settings + aggregation

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/settings` | — | `{extra_background_paths, global_negative_phrases}` |
| `PATCH` | `/settings` | partial of above | full snapshot after apply |
| `GET` | `/status` | — | aggregated snapshot (models + train + record + backgrounds + corpora + wsl_available) |
| `GET` | `/health` | — | `{ok, version, gpu, gpu_free_mb, python}` |

## Use with JarvYZ

JarvYZ's `web/api/wakeword_dev.py` is a thin adapter that forwards to this satellite.
Configure via `wakeword.trainer_url` (default `http://127.0.0.1:9001`).

When `trainer_url` is localhost AND the satellite isn't running, JarvYZ will auto-spawn
it. When it's remote, JarvYZ surfaces a clear "unreachable" error.

## Architecture

```
HTTP client (JarvYZ / curl / userscript)
        │
        ▼
   server.py  (FastAPI)
        │
        ▼
   trainer.py   ←  spawns openwakeword as a Python subprocess
        │            (Popen with start_new_session=True / CREATE_NEW_PROCESS_GROUP)
        ▼
   openwakeword.train  (with patches/* applied)
```

The trainer owns: session file, log file, metrics capture, subprocess lifecycle.
The server owns: HTTP/WS surface, job state, request validation.
JarvYZ owns: nothing about training. Just talks HTTP.

## Data layout

Everything the satellite writes lives under `<wakeword_root>` — default
`~/.jarvyz/satellites/wakeword-trainer/`. The data dir is keyed by the
satellite id `wakeword-trainer` (the source folder is now
`satellites/yz-wakeword-trainer/`); keeping the data dir name stable means
no migration. It keeps trainer data cleanly separate from JarvYZ's own
app-data (settings.json, transcript/, people/,
openwakeword/ deploy target) directly under `~/.jarvyz/`.

Override via `JWT_WAKEWORD_ROOT` env. Subdirs:

```
<wakeword_root>/
├── settings.json                    # mutable settings (UI-editable)
├── .runtime_config.yaml             # rendered per-job training config
├── train.log                        # subprocess stdout/stderr
├── record.log                       # /record/start subprocess output
├── train_session.json               # active training job (pid, slug, ...)
├── record_session.json              # active record session (pid, ...)
├── models/<slug>.json               # per-model meta (phrase, lang, history, ...)
├── runs/<slug>/                     # generated WAVs, npy features
├── runs/<slug>.onnx                 # trained model output
└── corpora/
    ├── backgrounds/loom_room/       # room recordings (from /record/*)
    ├── backgrounds/{fma_small,...}  # downloaded background packs
    └── rirs/mit_ir_survey/Audio/    # impulse responses
```

## vad_in_training (per-slug override)

Set on a per-slug train config to apply Silero VAD trim+repad to every
positive clip before training. Useful when the corpus has variable
leading/trailing silence (e.g. piper-synthesized positives that breathe
slightly off-beat). Pipeline:

1. On `/train`, if `overrides.vad_in_training: true` is set for the slug,
   `pipeline/vad_preprocess.py` walks the slug's positive WAVs.
2. Each clip is trimmed to the first VAD-detected speech segment and
   repadded to exactly 2 s @ 16 kHz mono.
3. A sibling `.vad_processed` marker is written next to each clip → the
   pass is idempotent across retrains.
4. The marker is cleared on `wipe_stale=true` (so clip_gen regen
   reprocesses fresh outputs).

When the override is OFF but the marker exists (mixed state), a WARN is
logged so the operator notices they're training on previously-trimmed
clips that won't be re-cleaned.

**Destructive:** overwrites positives in place. Reversal path is
`wipe_stale=true` + clip_gen regen.

## Patches

`patches/` contains idempotent shims for upstream issues. The list grows
as we hit new compat bumps; the authoritative source is the directory
listing, not this README. Notable ones:

- `patch_scipy_acoustics.py` — aliases `scipy.special.sph_harm` to
  `sph_harm_y` so `import acoustics` works on scipy ≥ 1.16. No-op when
  not needed.
- `patch_oww_tflite.py` — comments out OWW's unconditional onnx→tflite
  conversion (we never want it; onnx_tf is intentionally not installed).
- `patch_oww_data.py` — clamps `n_words` in adversarial-text sampling to
  handle short phrase lists.
- `patch_torch_audiomentations_*.py` — keeps OWW's augmentation pipeline
  compatible with torch-audiomentations ≥ current.
- `patch_oww_train_imports.py`, `patch_oww_dataloader_workers.py`,
  `patch_oww_trim_mmap.py` — Windows-native-training compat (no
  multiprocessing workers, mmap quirks, import order).

Applied automatically at training spawn (`trainer.py`).

</details>
