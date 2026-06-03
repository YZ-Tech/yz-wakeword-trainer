import { useEffect, useState } from 'react'

/** Live log view backed by a 1-Hz poll of the log endpoint.
 *
 *  Why polling and not WS: openwakeword's tqdm trainer fires ~50
 *  progress lines per second. The JarvYZ event bus is per-subscriber
 *  bounded queue + serialized fan-out; under that load the browser
 *  drops behind, the queue fills, and chunks are silently dropped —
 *  the UI freezes mid-training. Polling /train/log returns the entire
 *  tail in one shot, naturally rate-limited by the 1-Hz interval. The
 *  payload is small (~10 KB) and the request is local.
 *
 *  Behavior:
 *    - When `enabled` flips true, start the poll (immediate + every 1s).
 *    - When `enabled` flips false (process exited / crashed),
 *      stop polling but PRESERVE the text so the user can read the
 *      final state — including the error message that caused the
 *      crash.
 *    - `resetKey` is a hard reset: when it changes (e.g., user picks
 *      a different model), clear the panel.
 */
// Progress-line collapse. Two formats get collapsed into a single in-place
// updating row each:
//
//   1. tqdm percentage bars from openwakeword
//      "Computing features:  43%|████  | 670/1562 [...]"
//      "Training:  78%|████  | 38751/50000 [...]"
//      → label = the prefix word(s) ("Computing features", "Training")
//
//   2. clip_gen watcher heartbeat (appended by the bash watcher while
//      piper-tts is synthesizing WAVs and the trainer subprocess hasn't
//      spawned yet — see the `bu2xmpb3d`-style monitor):
//      "[progress 12:56:47] positive_train: 5520/50000   positive_test: ..."
//      → all such lines share one label so the latest one replaces the rest
//
// Without this, the LogPanel becomes an unreadable wall of nearly-identical
// rows.
const TQDM_PROGRESS_RE = /^([A-Za-z][\w ]+):\s+\d+%\|/
const WATCHER_PROGRESS_RE = /^\[progress \d{1,2}:\d{2}:\d{2}\]/

function collapseProgress(text: string): string {
  const inLines = text.split('\n')
  const out: string[] = []
  const lastIdxByLabel = new Map<string, number>()
  for (const line of inLines) {
    let label: string | undefined
    const mTqdm = line.match(TQDM_PROGRESS_RE)
    if (mTqdm) {
      label = mTqdm[1]
    } else if (WATCHER_PROGRESS_RE.test(line)) {
      label = '__watcher_progress__'  // single shared bucket
    }
    if (label === undefined) {
      out.push(line)
      continue
    }
    const prior = lastIdxByLabel.get(label)
    if (prior !== undefined) {
      out[prior] = line  // replace the older same-label entry in place
    } else {
      lastIdxByLabel.set(label, out.length)
      out.push(line)
    }
  }
  return out.join('\n')
}

export function useLogTail(
  fetcher: () => Promise<string>,
  enabled: boolean,
  resetKey?: string | number,
) {
  const [text, setText] = useState<string>('')

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setText('')
  }, [resetKey])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const fetchOnce = async () => {
      try {
        const log = await fetcher()
        if (cancelled) return
        setText(collapseProgress(log))
      } catch {
        /* ignore transient failures */
      }
    }
    fetchOnce()
    const id = window.setInterval(fetchOnce, 1000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [enabled, fetcher])

  return text
}
