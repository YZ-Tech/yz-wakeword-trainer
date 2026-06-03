import { useState } from 'react'
import {
  Box,
  Button,
  Card,
  CardContent,
  CardHeader,
  Chip,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import CompareIcon from '@mui/icons-material/Compare'
import DeleteIcon from '@mui/icons-material/Delete'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import UploadIcon from '@mui/icons-material/Upload'
import { Fragment } from 'react'
import { useApi } from '../lib/api'
import { fmtAgo, fmtBytes } from '../lib/fmt'
import type { ModelView, TrainingRun } from '../types'
import { CompareModelsDialog } from './CompareModelsDialog'
import { NewModelDialog } from './NewModelDialog'

/** Extract (accuracy | recall | fp_per_hour) from a run row, handling
 *  both new (nested `metrics.*`) and legacy (flat fields) shapes. */
function metricOf(r: TrainingRun, k: 'accuracy' | 'recall' | 'fp_per_hour'): number | undefined {
  return r.metrics?.[k] ?? r[k]
}

/** Subline status text for a model row — consolidates what used to be 3-4
 *  separate chips (deployed / newer-than-deployed / orphan) into a single
 *  line with color-coded text. The chip row stays clean for metrics only. */
function renderModelStatusLine(m: ModelView, hasOnnx: boolean, isNewer: boolean): React.ReactNode {
  let deployText: React.ReactNode = null
  if (m.deployed.exists && !isNewer) {
    deployText = <Box component="span" sx={{ color: 'success.main' }}>deployed</Box>
  } else if (isNewer) {
    deployText = <Box component="span" sx={{ color: 'warning.main' }}>deploy pending</Box>
  } else if (hasOnnx) {
    deployText = <Box component="span" sx={{ color: 'text.disabled' }}>not deployed</Box>
  } else {
    deployText = <Box component="span" sx={{ color: 'text.disabled', fontStyle: 'italic' }}>not trained yet</Box>
  }

  const orphanTag = !m.has_meta ? (
    <Tooltip title="No JSON meta — synthesized from disk (legacy or external)">
      <Box component="span" sx={{ color: 'text.disabled', textDecoration: 'underline dotted', cursor: 'help' }}>
        orphan
      </Box>
    </Tooltip>
  ) : null

  return (
    <>
      {deployText}
      {orphanTag && <> · {orphanTag}</>}
      {hasOnnx && <> · {fmtBytes(m.dataset.wsl_onnx_size)} · trained {fmtAgo(m.dataset.wsl_onnx_mtime)}</>}
      {' · '}lang {m.language}
    </>
  )
}

/** Tooltip body for a metric chip — current value + trend across last 3 runs.
 *  Returned as a React fragment so newlines actually render in the MUI tooltip. */
function renderMetricTooltip(
  key: 'accuracy' | 'recall' | 'fp_per_hour',
  m: ModelView,
): React.ReactNode {
  const label = key === 'accuracy' ? 'Accuracy' : key === 'recall' ? 'Recall' : 'FP/hour'
  const fmt = (v: number) =>
    key === 'fp_per_hour' ? `${v.toFixed(1)}/h` : `${(v * 100).toFixed(2)}%`
  const cur = m.metrics?.[key] ?? 0
  const hist = (m.training_history ?? []).filter((r) => metricOf(r, key) !== undefined)
  const recent = hist.slice(-4).reverse() // newest first, up to 4

  return (
    <Box sx={{ minWidth: 180 }}>
      <Box sx={{ fontWeight: 600, mb: 0.5 }}>
        {label}: {fmt(cur)}
      </Box>
      <Box sx={{ fontSize: '0.7rem', opacity: 0.85 }}>
        trained {fmtAgo(m.metrics?.trained_at ?? 0)}
      </Box>
      {recent.length > 1 && (
        <>
          <Box sx={{ mt: 1, mb: 0.25, fontSize: '0.7rem', opacity: 0.7 }}>
            Recent runs ({hist.length}):
          </Box>
          <Box sx={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.7rem' }}>
            {recent.map((r, i) => {
              const v = metricOf(r, key)!
              const prev = i + 1 < recent.length ? metricOf(recent[i + 1], key) : undefined
              const delta =
                prev !== undefined && key !== 'fp_per_hour'
                  ? (v - prev) * 100
                  : prev !== undefined
                    ? v - prev
                    : undefined
              const arrow =
                delta === undefined
                  ? ' '
                  : (key === 'fp_per_hour' ? delta < 0 : delta > 0)
                    ? '↑'
                    : delta === 0
                      ? '·'
                      : '↓'
              return (
                <Fragment key={r.ts ?? r.trained_at ?? i}>
                  {fmt(v)} {arrow}{' '}
                  {delta !== undefined
                    ? `(${delta > 0 ? '+' : ''}${key === 'fp_per_hour' ? delta.toFixed(1) : delta.toFixed(2)})`
                    : ''}
                  {'  '}
                  <span style={{ opacity: 0.6 }}>
                    {fmtAgo(r.ts ?? r.trained_at ?? 0)}
                  </span>
                  <br />
                </Fragment>
              )
            })}
          </Box>
        </>
      )}
    </Box>
  )
}

interface Props {
  models: ModelView[]
  selectedSlug: string
  onSelect: (slug: string) => void
  trainingSlug: string
  onError: (msg: string) => void
  onAfterAction: () => void | Promise<void>
}

export function ModelsCard({
  models,
  selectedSlug,
  onSelect,
  trainingSlug,
  onError,
  onAfterAction,
}: Props) {
  const [newOpen, setNewOpen] = useState(false)
  const [compareOpen, setCompareOpen] = useState(false)
  const [busySlug, setBusySlug] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const api = useApi()

  const handleDeploy = async (slug: string) => {
    setBusySlug(slug)
    try {
      await api.deployModel(slug)
      await onAfterAction()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusySlug(null)
    }
  }

  const handleDelete = async (slug: string) => {
    setBusySlug(slug)
    try {
      await api.deleteModel(slug)
      setConfirmDelete(null)
      if (selectedSlug === slug) {
        const next = models.find((m) => m.slug !== slug)
        onSelect(next ? next.slug : '')
      }
      await onAfterAction()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusySlug(null)
    }
  }

  return (
    <Card variant="outlined">
      <CardHeader
        title={
          <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
            <span>Wake words</span>
            <Tooltip title="Each wake word is a separate model with its own dataset (TTS-synthesized phrases), metrics, and deployed .onnx. Click a row to load it into the Trainer. Metric chips: acc/recall/FP per hour (lower FP is better).">
              <IconButton size="small" sx={{ p: 0.25 }}>
                <InfoOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        }
        subheader={`${models.length} model${models.length === 1 ? '' : 's'}`}
        action={
          <Stack direction="row" sx={{ gap: 0.5 }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<CompareIcon />}
              onClick={() => setCompareOpen(true)}
              disabled={models.length < 1}
            >
              Compare
            </Button>
            <Button
              size="small"
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => setNewOpen(true)}
            >
              New
            </Button>
          </Stack>
        }
        sx={{ pb: 0 }}
      />
      <CardContent>
        {models.length === 0 ? (
          <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
            No wake words yet. Click "New" to define one.
          </Typography>
        ) : (
          <Stack sx={{ gap: 0.5 }}>
            {models.map((m) => {
              const isSelected = selectedSlug === m.slug
              const isTraining = trainingSlug === m.slug
              const hasOnnx = (m.dataset.wsl_onnx_size ?? 0) > 0
              const isNewer =
                hasOnnx && m.dataset.wsl_onnx_mtime > (m.deployed.mtime ?? 0)
              return (
                <Box
                  key={m.slug}
                  onClick={() => onSelect(m.slug)}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    p: 1,
                    borderRadius: 1,
                    border: 1,
                    borderColor: isSelected ? 'primary.main' : 'divider',
                    bgcolor: isSelected ? 'action.selected' : 'transparent',
                    cursor: 'pointer',
                    '&:hover': { bgcolor: 'action.hover' },
                  }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    {/* Top line: name + slug · training chip (only while active) ·
                        metric chips pushed to the right. Keeps the chip row
                        for the 3 quantities that change between runs (acc /
                        recall / FP per hour); status info migrated to the
                        subline. */}
                    <Stack direction="row" sx={{ alignItems: 'center', gap: 0.75 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {m.phrase}
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{
                          fontFamily: 'ui-monospace, monospace',
                          color: 'text.disabled',
                        }}
                      >
                        {m.slug}
                      </Typography>
                      {isTraining && (
                        <Chip
                          size="small"
                          label="training"
                          color="primary"
                          sx={{ height: 18, fontSize: '0.65rem' }}
                        />
                      )}
                      <Box sx={{ flex: 1 }} />
                      {m.metrics && (
                        <>
                          <Tooltip title={renderMetricTooltip('accuracy', m)}>
                            <Chip
                              size="small"
                              label={`acc ${(m.metrics.accuracy * 100).toFixed(0)}%`}
                              color={
                                m.metrics.accuracy >= 0.85
                                  ? 'success'
                                  : m.metrics.accuracy >= 0.70
                                    ? 'warning'
                                    : 'error'
                              }
                              variant="outlined"
                              sx={{ height: 18, fontSize: '0.65rem' }}
                            />
                          </Tooltip>
                          <Tooltip title={renderMetricTooltip('recall', m)}>
                            <Chip
                              size="small"
                              label={`rec ${(m.metrics.recall * 100).toFixed(0)}%`}
                              color={
                                m.metrics.recall >= 0.75
                                  ? 'success'
                                  : m.metrics.recall >= 0.50
                                    ? 'warning'
                                    : 'error'
                              }
                              variant="outlined"
                              sx={{ height: 18, fontSize: '0.65rem' }}
                            />
                          </Tooltip>
                          <Tooltip title={renderMetricTooltip('fp_per_hour', m)}>
                            <Chip
                              size="small"
                              label={`fp ${Math.round(m.metrics.fp_per_hour)}/h`}
                              color={
                                m.metrics.fp_per_hour < 100
                                  ? 'success'
                                  : m.metrics.fp_per_hour < 300
                                    ? 'warning'
                                    : 'error'
                              }
                              variant="outlined"
                              sx={{ height: 18, fontSize: '0.65rem' }}
                            />
                          </Tooltip>
                        </>
                      )}
                    </Stack>
                    {/* Subline: consolidated status text. Color-coded for
                        deployment health — no more separate chips for
                        deployed / newer-than-deployed / orphan. */}
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: 'block' }}
                    >
                      {renderModelStatusLine(m, hasOnnx, isNewer)}
                    </Typography>
                  </Box>
                  <Tooltip title="Deploy → Windows">
                    <span>
                      <IconButton
                        size="small"
                        disabled={!hasOnnx || busySlug === m.slug}
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDeploy(m.slug)
                        }}
                      >
                        <UploadIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                  {confirmDelete === m.slug ? (
                    <Stack direction="row" sx={{ gap: 0.5 }} onClick={(e) => e.stopPropagation()}>
                      <Button
                        size="small"
                        color="error"
                        variant="contained"
                        disabled={busySlug === m.slug}
                        onClick={() => handleDelete(m.slug)}
                      >
                        Delete
                      </Button>
                      <Button
                        size="small"
                        onClick={() => setConfirmDelete(null)}
                        disabled={busySlug === m.slug}
                      >
                        Cancel
                      </Button>
                    </Stack>
                  ) : (
                    <Tooltip title="Delete model (purges dataset + onnx + deployed)">
                      <IconButton
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmDelete(m.slug)
                        }}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                </Box>
              )
            })}
          </Stack>
        )}
      </CardContent>
      <NewModelDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={(slug) => {
          onSelect(slug)
          onAfterAction()
        }}
      />
      <CompareModelsDialog
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        initialSelection={selectedSlug ? [selectedSlug] : []}
        models={models}
        onAfterAction={onAfterAction}
      />
    </Card>
  )
}
