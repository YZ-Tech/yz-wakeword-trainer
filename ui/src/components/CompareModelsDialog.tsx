import { useEffect, useMemo, useState } from 'react'
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
// Chip is used by ModelColumn for the "deployed" badge; Autocomplete renders its own default chips.
import { useApi } from '../lib/api'
import { fmtAgo, fmtBytes } from '../lib/fmt'
import type { ModelView, TrainingRun } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  /** Slugs to pre-populate the picker with on open. */
  initialSelection?: string[]
  /** Full model list (passed in to avoid an extra round-trip). */
  models: ModelView[]
  onAfterAction: () => void | Promise<void>
}

function metricOf(r: TrainingRun, k: 'accuracy' | 'recall' | 'fp_per_hour'): number | undefined {
  return r.metrics?.[k] ?? r[k]
}

/** Color a phrase by how shared it is across the selected columns.
 *  unique → bold red. minority → yellow. majority → green. shared → muted. */
function phraseColor(presence: number, total: number): {
  bg: string
  fg: string
  weight: number
} {
  if (presence === total) return { bg: 'transparent', fg: 'text.disabled', weight: 400 }
  if (presence === 1) return { bg: 'error.dark', fg: 'error.contrastText', weight: 700 }
  const ratio = presence / total
  return ratio >= 0.5
    ? { bg: 'success.dark', fg: 'success.contrastText', weight: 500 }
    : { bg: 'warning.dark', fg: 'warning.contrastText', weight: 500 }
}

/** Reusable side-by-side / multi-column comparison.
 *  Picker is MUI Autocomplete (multi). Body columns scale to selection. */
export function CompareModelsDialog({
  open,
  onClose,
  initialSelection,
  models,
  onAfterAction,
}: Props) {
  const [selected, setSelected] = useState<string[]>([])
  const api = useApi()
  const [busyDeploy, setBusyDeploy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setSelected(initialSelection ?? [])
      setError(null)
    }
  }, [open, initialSelection])

  const slugOptions = useMemo(() => models.map((m) => m.slug), [models])
  const selectedModels = useMemo(
    () => selected.map((s) => models.find((m) => m.slug === s)).filter((m): m is ModelView => !!m),
    [selected, models],
  )

  // Union of all negatives across selected models. For each phrase, count
  // presence to drive the color heatmap.
  const phraseTable = useMemo(() => {
    const rows: { phrase: string; presence: number; perSlug: Record<string, boolean> }[] = []
    if (selectedModels.length === 0) return rows
    const union = new Set<string>()
    for (const m of selectedModels) for (const p of m.negatives ?? []) union.add(p)
    for (const p of [...union].sort()) {
      const perSlug: Record<string, boolean> = {}
      let presence = 0
      for (const m of selectedModels) {
        const has = (m.negatives ?? []).includes(p)
        perSlug[m.slug] = has
        if (has) presence += 1
      }
      rows.push({ phrase: p, presence, perSlug })
    }
    return rows
  }, [selectedModels])

  const handleDeploy = async (slug: string) => {
    setBusyDeploy(slug)
    setError(null)
    try {
      await api.deployModel(slug)
      await onAfterAction()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyDeploy(null)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xl" fullWidth scroll="paper">
      <DialogTitle sx={{ pb: 1 }}>Compare wake-word models</DialogTitle>
      <DialogContent>
        <Autocomplete
          multiple
          options={slugOptions}
          value={selected}
          onChange={(_, v) => setSelected(v)}
          renderInput={(params) => (
            <TextField {...params} placeholder="Select 2+ slugs to compare" size="small" />
          )}
          sx={{ mb: 2 }}
        />

        {error && (
          <Typography variant="caption" color="error.main" sx={{ display: 'block', mb: 1 }}>
            {error}
          </Typography>
        )}

        {selectedModels.length === 0 ? (
          <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
            Pick at least one model from the picker above. Compare 2+ to see negatives diff.
          </Typography>
        ) : (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: `repeat(${selectedModels.length}, minmax(260px, 1fr))`,
              gap: 2,
            }}
          >
            {selectedModels.map((m) => (
              <ModelColumn
                key={m.slug}
                model={m}
                onDeploy={() => handleDeploy(m.slug)}
                deploying={busyDeploy === m.slug}
              />
            ))}
          </Box>
        )}

        {selectedModels.length >= 2 && phraseTable.length > 0 && (
          <>
            <Divider sx={{ my: 3 }} />
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
              Negatives diff — {phraseTable.length} unique phrases across selection
            </Typography>
            <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mb: 1 }}>
              Color: red = unique to one model · yellow = minority · green = majority · muted = shared by all
            </Typography>
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: `minmax(160px, 2fr) repeat(${selectedModels.length}, minmax(40px, 80px))`,
                gap: 0.25,
                fontSize: '0.7rem',
                fontFamily: 'ui-monospace, monospace',
              }}
            >
              <Box sx={{ fontWeight: 600, pb: 0.5 }}>phrase</Box>
              {selectedModels.map((m) => (
                <Box key={m.slug} sx={{ fontWeight: 600, pb: 0.5, textAlign: 'center' }}>
                  {m.slug}
                </Box>
              ))}
              {phraseTable.map(({ phrase, presence, perSlug }) => {
                const color = phraseColor(presence, selectedModels.length)
                return (
                  <Box key={phrase} sx={{ display: 'contents' }}>
                    <Box
                      sx={{
                        px: 0.5,
                        py: 0.25,
                        bgcolor: color.bg,
                        color: color.fg,
                        fontWeight: color.weight,
                        borderRadius: 0.5,
                      }}
                    >
                      {phrase}
                    </Box>
                    {selectedModels.map((m) => (
                      <Box
                        key={m.slug}
                        sx={{
                          textAlign: 'center',
                          py: 0.25,
                          opacity: perSlug[m.slug] ? 1 : 0.2,
                        }}
                      >
                        {perSlug[m.slug] ? '●' : '○'}
                      </Box>
                    ))}
                  </Box>
                )
              })}
            </Box>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ModelColumn({
  model,
  onDeploy,
  deploying,
}: {
  model: ModelView
  onDeploy: () => void
  deploying: boolean
}) {
  const m = model
  const hist = (m.training_history ?? []).slice().reverse() // newest first
  const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`
  return (
    <Box sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5 }}>
      <Stack direction="row" sx={{ alignItems: 'baseline', gap: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1, minWidth: 0 }}>
          {m.phrase}
        </Typography>
        {m.deployed.exists && (
          <Tooltip title={`Deployed: ${m.deployed.path}`}>
            <Chip size="small" label="deployed" color="success" sx={{ height: 18, fontSize: '0.65rem' }} />
          </Tooltip>
        )}
      </Stack>
      <Typography variant="caption" color="text.disabled" sx={{ fontFamily: 'ui-monospace, monospace' }}>
        {m.slug} · {m.language} · {(m.negatives ?? []).length} negs
      </Typography>

      <Divider sx={{ my: 1 }} />

      {m.metrics ? (
        <Stack sx={{ gap: 0.25 }}>
          <Typography variant="caption" sx={{ fontFamily: 'ui-monospace, monospace' }}>
            acc: <b>{fmtPct(m.metrics.accuracy)}</b>
          </Typography>
          <Typography variant="caption" sx={{ fontFamily: 'ui-monospace, monospace' }}>
            rec: <b>{fmtPct(m.metrics.recall)}</b>
          </Typography>
          <Typography variant="caption" sx={{ fontFamily: 'ui-monospace, monospace' }}>
            fp:  <b>{m.metrics.fp_per_hour.toFixed(1)}/h</b>
          </Typography>
          <Typography variant="caption" color="text.disabled">
            trained {fmtAgo(m.metrics.trained_at)}
          </Typography>
        </Stack>
      ) : (
        <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
          not trained under metrics capture yet
        </Typography>
      )}

      <Divider sx={{ my: 1 }} />

      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
        Run history ({hist.length})
      </Typography>
      {hist.length === 0 ? (
        <Typography variant="caption" color="text.disabled" sx={{ display: 'block', fontStyle: 'italic' }}>
          no runs recorded
        </Typography>
      ) : (
        <Box
          sx={{
            maxHeight: 200,
            overflowY: 'auto',
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.65rem',
            mt: 0.5,
          }}
        >
          {hist.map((r, i) => {
            const acc = metricOf(r, 'accuracy')
            const rec = metricOf(r, 'recall')
            const fp = metricOf(r, 'fp_per_hour')
            const ts = r.ts ?? r.trained_at ?? 0
            return (
              <Box key={i} sx={{ borderBottom: 1, borderColor: 'divider', py: 0.5 }}>
                <Box sx={{ opacity: 0.7 }}>{fmtAgo(ts)}</Box>
                <Box>
                  acc={acc !== undefined ? fmtPct(acc) : '—'}{' '}
                  rec={rec !== undefined ? fmtPct(rec) : '—'}{' '}
                  fp={fp !== undefined ? `${fp.toFixed(1)}/h` : '—'}
                </Box>
                {r.negatives_count !== undefined && (
                  <Box sx={{ opacity: 0.6 }}>
                    {r.negatives_count} negs · {r.negatives_hash ?? ''}
                  </Box>
                )}
                {r.elapsed_seconds !== undefined && r.config?.n_samples !== undefined && (
                  <Box sx={{ opacity: 0.6 }}>
                    n={r.config.n_samples} · {Math.round(r.elapsed_seconds)}s
                    {r.onnx_size ? ` · ${fmtBytes(r.onnx_size)}` : ''}
                  </Box>
                )}
              </Box>
            )
          })}
        </Box>
      )}

      <Divider sx={{ my: 1 }} />

      <Button
        size="small"
        variant="outlined"
        fullWidth
        disabled={deploying || !m.deployed.exists ? false : false}
        onClick={onDeploy}
      >
        {deploying
          ? 'Deploying…'
          : m.deployed.exists
            ? `Re-deploy ${m.slug}`
            : `Deploy ${m.slug}`}
      </Button>
    </Box>
  )
}
