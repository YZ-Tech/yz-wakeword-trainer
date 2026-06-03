// Semantic API contract for the wake-word trainer module.
//
// The module knows nothing about URLs. It declares the operations it
// needs as named methods; the host provides an implementation. This
// decouples the module from any specific server's path scheme.
//
// Adapters shipped with the module:
//   - createJarvYZApi()    — wraps JarvYZ's /api/wakeword_dev/* namespace
//   - createSatelliteApi() — Phase 2: wraps satellite native routes
//
// A host can also write its own adapter that implements WakeWordApi.

import { createContext, useContext } from 'react'
import type { CorpusView, WakewordStatus } from '../types'
import type { TrainPreflight } from '../components/TrainPreflightDialog'

export interface CorporaSnapshot {
  corpora: CorpusView[]
  ready: { ready: boolean; missing: string[] }
}

export interface CreateModelInput {
  phrase: string
  language: string
  slug: string
}

export interface StartTrainOptions {
  wipeStale?: boolean
}

export interface GetLogOptions {
  tail?: number
}

export interface SpikeStatus {
  running: boolean
  slug: string
  started_at: number
  latest_score: number
  peak_score: number
  error: string
  /** Recent (timestamp, score) pairs for sparkline rendering. */
  recent: [number, number][]
}

export interface AudioDevice {
  index: number
  name: string
  max_input_channels: number
  host_api: number
  default_samplerate: number
}

export interface AudioHostApi {
  index: number
  name: string
}

export interface AudioDevicesResponse {
  devices: AudioDevice[]
  host_apis: AudioHostApi[]
  default_input: number | null
}

export interface CorpusActionBody {
  corpus: string
}

export interface SettingsSnapshot {
  extra_background_paths?: string[]
  [k: string]: unknown
}

/** The complete API surface the module needs from its host.
 *  All methods throw on failure (Error with backend message). */
export interface WakeWordApi {
  // Status / aggregation
  getStatus(): Promise<WakewordStatus>

  // Models
  createModel(input: CreateModelInput): Promise<{ ok: boolean; slug: string }>
  cloneModel(sourceSlug: string, destSlug: string): Promise<{ slug: string }>
  deleteModel(slug: string): Promise<void>
  deployModel(slug: string): Promise<void>
  /** Standalone-only alternative to deployModel. Returns a blob URL or
   *  triggers a browser download. Hosts that don't support download
   *  may throw. */
  downloadOnnx(slug: string): Promise<void>

  // Negatives (text phrases that should NOT trigger the wake word)
  getGlobalNegatives(): Promise<string[]>
  getModelNegatives(slug: string): Promise<string[]>
  setGlobalNegatives(phrases: string[]): Promise<void>
  setModelNegatives(slug: string, phrases: string[]): Promise<void>

  // Training
  getTrainPreflight(slug: string): Promise<TrainPreflight>
  startTrain(slug: string, opts?: StartTrainOptions): Promise<void>
  stopTrain(): Promise<void>
  getTrainLog(opts?: GetLogOptions): Promise<string>

  // Room records (background audio)
  startRoomRecord(minutes: number): Promise<void>
  stopRoomRecord(): Promise<void>
  getRoomRecordLog(opts?: GetLogOptions): Promise<string>
  deleteBackground(name: string): Promise<void>

  // Spike — live model test (loads ONNX, runs against satellite mic)
  startSpike(slug: string, device?: number | string | null): Promise<SpikeStatus>
  stopSpike(): Promise<SpikeStatus>
  getSpikeStatus(): Promise<SpikeStatus>
  listAudioInputs(): Promise<AudioDevicesResponse>

  // Corpora (training audio packs)
  getCorporaSnapshot(): Promise<CorporaSnapshot>
  startCorporaDownload(corpus: string): Promise<void>
  cancelCorporaDownload(corpus: string): Promise<void>

  // Settings
  getSettings(): Promise<SettingsSnapshot>
  patchSettings(patch: Partial<SettingsSnapshot>): Promise<SettingsSnapshot>
}

// ---------------------------------------------------------------------------
// Default (no-op) implementation — used when no api prop is provided.
// Every method throws. This catches "you forgot to wire the api" early.

const stub = <T>(name: string): Promise<T> =>
  Promise.reject(new Error(`WakeWordApi.${name}() called but no api prop provided`))

const NO_API: WakeWordApi = {
  getStatus: () => stub('getStatus'),
  createModel: () => stub('createModel'),
  cloneModel: () => stub('cloneModel'),
  deleteModel: () => stub('deleteModel'),
  deployModel: () => stub('deployModel'),
  downloadOnnx: () => stub('downloadOnnx'),
  getGlobalNegatives: () => stub('getGlobalNegatives'),
  getModelNegatives: () => stub('getModelNegatives'),
  setGlobalNegatives: () => stub('setGlobalNegatives'),
  setModelNegatives: () => stub('setModelNegatives'),
  getTrainPreflight: () => stub('getTrainPreflight'),
  startTrain: () => stub('startTrain'),
  stopTrain: () => stub('stopTrain'),
  getTrainLog: () => stub('getTrainLog'),
  startRoomRecord: () => stub('startRoomRecord'),
  stopRoomRecord: () => stub('stopRoomRecord'),
  getRoomRecordLog: () => stub('getRoomRecordLog'),
  deleteBackground: () => stub('deleteBackground'),
  startSpike: () => stub('startSpike'),
  stopSpike: () => stub('stopSpike'),
  getSpikeStatus: () => stub('getSpikeStatus'),
  listAudioInputs: () => stub('listAudioInputs'),
  getCorporaSnapshot: () => stub('getCorporaSnapshot'),
  startCorporaDownload: () => stub('startCorporaDownload'),
  cancelCorporaDownload: () => stub('cancelCorporaDownload'),
  getSettings: () => stub('getSettings'),
  patchSettings: () => stub('patchSettings'),
}

export const ApiContext = createContext<WakeWordApi>(NO_API)

export const useApi = () => useContext(ApiContext)

// ---------------------------------------------------------------------------
// JarvYZ adapter — wraps /api/wakeword_dev/* paths via fetch.
// Both JarvYZ-embedded mode and (Phase 1) the standalone SPA use this.
// Phase 2: a satellite-native adapter that calls /train, /models, etc.

interface HttpClient {
  request<T>(method: string, path: string, body?: unknown): Promise<T>
}

function httpClient(apiBase: string): HttpClient {
  return {
    async request<T>(method: string, path: string, body?: unknown): Promise<T> {
      const url = apiBase + path
      const res = await fetch(url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      })
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        throw new Error(`${method} ${url} → ${res.status} ${detail}`)
      }
      // 204 / empty body → undefined as T
      const text = await res.text()
      return (text ? JSON.parse(text) : undefined) as T
    },
  }
}

// ---------------------------------------------------------------------------
// Satellite adapter — wraps the satellite's native routes via fetch.
// Used by the standalone SPA (App.tsx) when served directly by the
// satellite. Operations the satellite doesn't expose yet (record/*)
// throw NotSupportedError; consumers show "feature unavailable".

export class NotSupportedError extends Error {
  constructor(operation: string) {
    super(`Operation '${operation}' is not supported by this host`)
    this.name = 'NotSupportedError'
  }
}

export function createSatelliteApi({ apiBase = '' }: { apiBase?: string } = {}): WakeWordApi {
  const h = httpClient(apiBase)
  const enc = encodeURIComponent
  // Satellite uses a job-based model: each train is a /jobs/{id}.
  // We track the most-recent train job id locally so semantic stopTrain()
  // and getTrainLog() can target it. Refreshed on every startTrain.
  let currentTrainJobId: string | null = null

  return {
    getStatus: () => h.request('GET', '/status'),

    createModel: (input) =>
      h.request('POST', '/models', input),
    cloneModel: (src, dst) =>
      h.request('POST', `/models/${enc(src)}/clone`, { slug: dst }),
    deleteModel: (slug) =>
      h.request('DELETE', `/models/${enc(slug)}`),
    deployModel: () => {
      // Satellite has no "live JarvYZ runtime" to deploy to. In standalone
      // mode, the trainer UI's TrainerCard should be in 'download' mode
      // (capabilities.deployTarget='download') so this method is never
      // reached. Throwing makes the wiring error loud rather than silent.
      throw new NotSupportedError('deployModel')
    },
    downloadOnnx: async (slug) => {
      const a = document.createElement('a')
      a.href = `${apiBase}/models/${enc(slug)}/onnx`
      a.download = `${slug}.onnx`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    },

    getGlobalNegatives: async () => {
      const r = await h.request<{ phrases: string[] }>('GET', '/negatives')
      return r.phrases ?? []
    },
    getModelNegatives: async (slug) => {
      const r = await h.request<{ phrases: string[] }>(
        'GET',
        `/models/${enc(slug)}/negatives`,
      )
      return r.phrases ?? []
    },
    setGlobalNegatives: (phrases) =>
      h.request('PUT', '/negatives', { phrases }),
    setModelNegatives: (slug, phrases) =>
      h.request('PUT', `/models/${enc(slug)}/negatives`, { phrases }),

    getTrainPreflight: (slug) =>
      h.request('GET', `/train/preflight/${enc(slug)}`),
    startTrain: async (slug, opts) => {
      const r = await h.request<{ job_id: string }>('POST', '/train', {
        slug,
        wipe_stale: !!opts?.wipeStale,
      })
      currentTrainJobId = r.job_id
    },
    stopTrain: async () => {
      const jid = currentTrainJobId ?? 'current'  // satellite uses 'current' as the single-job sentinel
      await h.request('POST', `/jobs/${enc(jid)}/stop`)
    },
    getTrainLog: async (opts) => {
      const jid = currentTrainJobId ?? 'current'
      const tail = opts?.tail ?? 120
      const r = await h.request<{ log?: string; chunk?: string }>(
        'GET',
        `/jobs/${enc(jid)}/log?tail=${tail}`,
      )
      return r.log ?? r.chunk ?? ''
    },

    // Room ambient capture. The semantic op takes minutes only;
    // device picking is satellite-side (reads JarvYZ settings.json or
    // PortAudio default). Future audio-source selector could pipe the
    // device name through if needed.
    startRoomRecord: async (minutes) => {
      await h.request('POST', '/record/start', { minutes })
    },
    stopRoomRecord: async () => {
      await h.request('POST', '/record/stop')
    },
    getRoomRecordLog: async (opts) => {
      const tail = opts?.tail ?? 40
      const r = await h.request<{ log: string }>('GET', `/record/log?tail=${tail}`)
      return r.log
    },
    deleteBackground: (name) =>
      h.request('DELETE', `/backgrounds/${enc(name)}`),

    startSpike: (slug, device) =>
      h.request('POST', '/spike/start', { slug, device: device ?? null }),
    stopSpike: () => h.request('POST', '/spike/stop'),
    getSpikeStatus: () => h.request('GET', '/spike/status'),
    listAudioInputs: () => h.request('GET', '/audio/devices'),

    getCorporaSnapshot: () => h.request('GET', '/corpora/status'),
    startCorporaDownload: (corpus) =>
      h.request('POST', '/corpora/download', { corpus }),
    cancelCorporaDownload: (corpus) =>
      h.request('POST', '/corpora/cancel', { corpus }),

    getSettings: () => h.request('GET', '/settings'),
    patchSettings: (patch) =>
      h.request('PATCH', '/settings', patch),
  }
}


// Note on the JarvYZ adapter: the embedded JarvYZ loader builds its own
// adapter inline in frontend/src/pages/Dev/WakeWord/WakeWordTab.tsx using
// JarvYZ's existing api client (for shared auth/baseURL behavior). We
// previously exported a `createJarvYZApi()` here for symmetry, but it was
// dead code — never imported by any consumer. Removed 2026-05-30. If a
// future third-party host needs one, it's ~80 LOC of HTTP wrappers
// against /api/wakeword_dev/* paths.
