import { useCallback, useEffect, useState } from 'react'
import { useApi } from '../lib/api'
import { useSubscription } from '../lib/ws'
import type { WakewordStateEvent, WakewordStatus } from '../types'

const EMPTY_STATUS: WakewordStatus = {
  models: [],
  train: { running: false, cmd: '', slug: '' },
  record: { running: false, cmd: '' },
  backgrounds: { count: 0, rirs: 0, disk_bytes: 0, available: false, bg_list: [] },
  wsl_available: false,
}

/** Single source of truth for the WakeWord tab. NO POLLING.
 *
 *  Updates:
 *    1. One-shot fetch on mount.
 *    2. `wakeword_state` WS event from backend (any state transition) →
 *       re-fetch.
 *    3. Action handlers call `refresh()` directly after their RPC.
 */
export function useWakewordSnapshot() {
  const api = useApi()
  const [status, setStatus] = useState<WakewordStatus>(EMPTY_STATUS)
  const [error, setError] = useState<string | null>(null)
  // True once the first getStatus resolves — lets the tab tell "still
  // loading the initial snapshot" apart from "loaded and genuinely empty",
  // instead of flashing empty cards until data arrives.
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const s = await api.getStatus()
      setStatus(s)
      setError(null)
      setLoaded(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [api])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh()
  }, [refresh])

  useSubscription<WakewordStateEvent>('wakeword_state', () => {
    refresh()
  })

  return { status, error, loaded, refresh, setError }
}
