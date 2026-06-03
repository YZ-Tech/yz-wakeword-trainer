/** One row in a slug's training_history — rich record per /train call. */
export interface TrainingRun {
  ts: number
  config?: {
    n_samples?: number
    n_samples_val?: number
    augmentation_rounds?: number
    steps?: number
  }
  negatives_count?: number
  negatives_hash?: string
  elapsed_seconds?: number
  metrics?: {
    accuracy: number
    recall: number
    fp_per_hour: number
  }
  onnx_size?: number
  satellite_version?: string
  // Legacy rows (pre rich-record) used a flat {accuracy, recall, fp_per_hour, trained_at} shape.
  accuracy?: number
  recall?: number
  fp_per_hour?: number
  trained_at?: number
}

/** What the backend returns per model. */
export interface ModelView {
  slug: string
  phrase: string
  language: string
  negatives: string[]
  created_at: number
  has_meta: boolean
  dataset: {
    positive_train: number
    positive_test: number
    negative_train: number
    negative_test: number
    wsl_onnx_size: number
    wsl_onnx_mtime: number
  }
  deployed: {
    exists: boolean
    path: string
    size_bytes?: number
    mtime?: number
  }
  /** Latest training metrics, or null if never trained under metric capture. */
  metrics: {
    accuracy: number      // 0–1
    recall: number        // 0–1
    fp_per_hour: number   // count, lower = better
    trained_at: number    // epoch seconds
  } | null
  /** Per-run records (oldest → newest, capped at last 25). Drives history
   *  tooltips on metric chips + the Compare dialog's run table. */
  training_history?: TrainingRun[]
}

/** /api/wakeword_dev/status response shape (post multi-model refactor). */
export interface WakewordStatus {
  models: ModelView[]
  train: { running: boolean; cmd: string; slug: string }
  record: { running: boolean; cmd: string }
  backgrounds: {
    count: number
    rirs: number
    disk_bytes: number
    available: boolean
    bg_list: BackgroundItem[]
  }
  corpora?: {
    corpora: CorpusView[]
    ready: { ready: boolean; missing: string[] }
  }
  wsl_available: boolean
}

export interface CorpusView {
  name: string
  label: string
  url: string
  dest: string
  phase: 'idle' | 'downloading' | 'extracting' | 'complete' | 'error' | 'cancelled'
  present: boolean
  bytes_on_disk: number
  bytes_done: number
  bytes_total: number
  expected_bytes: number
  started_at: number
  finished_at: number
  error: string
}

export interface BackgroundItem {
  name: string
  size_bytes: number
  mtime: number
}

/** Backend WS event payload — fired on any wakeword state transition. */
export interface WakewordStateEvent {
  kind: string
}
