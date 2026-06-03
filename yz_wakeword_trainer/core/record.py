"""Ambient room-audio capture. Absorbed from the legacy
`tools/wakeword/record_room.py` so the satellite owns its own record path.

Records 16 kHz mono PCM for the requested duration, splits the result into
30-second chunks, and writes them to <wakeword_root>/corpora/backgrounds/loom_room/.
These chunks get mixed into training clips during augmentation so the model
learns to ignore THIS room's ambient signature (HVAC, fans, keyboard
clatter) rather than generic background audio.

Two ways to invoke:

  - **As a module**: `from .record import run_capture`. Called inline by
    a server process if you want the audio loop in-thread.
  - **As a subprocess**: `python -m yz_wakeword_trainer.core.record
    --minutes N [--device NAME]`. This is how the /record/start route
    spawns it — fresh process with its own SIGTERM-stop semantics, log
    redirected to <wakeword_root>/record.log.

The audio-device argument can be a string name (matched substring against
sounddevice's device list) or an int index. Default: read from
~/.jarvyz/settings.json's `audio.input_device` if present; else
sounddevice's OS default.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

from ..settings import settings, _jarvyz_home


CHUNK_SECONDS = 30
SAMPLE_RATE = 16_000


def _bg_dir() -> Path:
    return settings.corpora_dir / "backgrounds" / "loom_room"


def _resolve_device(device: str | int | None) -> str | int | None:
    """Resolve to whatever sounddevice accepts.
    - None → try JarvYZ settings.json, else None (PortAudio default).
    - str digits → int.
    - str non-digits → leave as-is (substring match against device list).
    - int → unchanged.
    """
    if device is None:
        try:
            settings_path = _jarvyz_home() / "settings.json"
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            d = data.get("audio", {}).get("input_device")
            if d:
                return d
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return None
    if isinstance(device, str) and device.isdigit():
        return int(device)
    return device


def run_capture(
    minutes: float,
    *,
    device: str | int | None = None,
    on_chunk: callable | None = None,
    should_stop: callable | None = None,
) -> int:
    """Capture `minutes` of audio in 30 s chunks. Returns chunk count.

    on_chunk(idx, path, elapsed_s, captured_samples, total_samples) is
    called after each chunk write. Useful for hooks; can be None.
    should_stop() returning True breaks the loop cleanly (used by the
    server when stopping via SIGTERM is too abrupt).
    """
    out_dir = _bg_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    total_samples = int(minutes * 60 * SAMPLE_RATE)
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dev = _resolve_device(device)
    print(f"recording {minutes:.1f} min @ {SAMPLE_RATE} Hz, device={dev!r} → {out_dir}")
    print("stay in the room behaving normally; do NOT speak. starting in 3...")
    for s in (3, 2, 1):
        print(f"  {s}")
        time.sleep(1)
    print("  GO")

    buf: list[np.ndarray] = []
    captured = 0
    chunk_idx = 0
    start = time.perf_counter()

    def on_audio(indata, _frames, _t, status):
        if status:
            print(f"[warn] {status}", file=sys.stderr)
        x = indata[:, 0].astype(np.float32)
        x_int16 = np.clip(x * 32768.0, -32768, 32767).astype(np.int16)
        buf.append(x_int16)

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=1024,
        device=dev,
        callback=on_audio,
    ):
        while captured < total_samples:
            if should_stop and should_stop():
                print("stop requested; flushing remainder")
                break
            time.sleep(0.5)
            if not buf:
                continue
            audio = np.concatenate(buf)
            buf.clear()
            captured += len(audio)
            while len(audio) >= chunk_samples:
                chunk = audio[:chunk_samples]
                audio = audio[chunk_samples:]
                out = out_dir / f"loom_room_{stamp}_{chunk_idx:03d}.wav"
                sf.write(str(out), chunk, SAMPLE_RATE, subtype="PCM_16")
                elapsed = time.perf_counter() - start
                pct = captured / total_samples * 100
                print(f"  chunk {chunk_idx:03d} → {out.name}  ({elapsed:.0f}s, {pct:.0f}%)")
                if on_chunk:
                    try:
                        on_chunk(chunk_idx, out, elapsed, captured, total_samples)
                    except Exception as e:  # noqa: BLE001
                        print(f"[warn] on_chunk hook failed: {e}", file=sys.stderr)
                chunk_idx += 1
            if len(audio):
                buf.insert(0, audio)
                captured -= len(audio)

    if buf:
        tail = np.concatenate(buf)
        out = out_dir / f"loom_room_{stamp}_{chunk_idx:03d}.wav"
        sf.write(str(out), tail, SAMPLE_RATE, subtype="PCM_16")
        chunk_idx += 1
        print(f"  tail {chunk_idx:03d} → {out.name}")

    print(f"done. {chunk_idx} chunks @ {CHUNK_SECONDS}s each in {out_dir}")
    return chunk_idx


def _main() -> None:
    # Windows console encoding fix (record_room.py used to do this).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minutes", type=float, default=10.0, help="recording duration in minutes")
    p.add_argument("--device", default=None, help="sounddevice device name or index (default: auto)")
    args = p.parse_args()
    run_capture(args.minutes, device=args.device)


if __name__ == "__main__":
    _main()
