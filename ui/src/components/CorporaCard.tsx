import { useEffect, useState } from 'react'
import {
  Box,
  Button,
  Card,
  CardContent,
  CardHeader,
  Chip,
  Divider,
  IconButton,
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import DownloadIcon from '@mui/icons-material/Download'
import StopIcon from '@mui/icons-material/Stop'
import { useApi } from '../lib/api'
import { fmtBytes } from '../lib/fmt'
import type { CorpusView, WakewordStatus } from '../types'

interface Props {
  status: WakewordStatus
  onError: (msg: string) => void
  onAfterAction: () => void | Promise<void>
}

const PHASE_COLOR: Record<CorpusView['phase'], 'default' | 'primary' | 'success' | 'warning' | 'error'> = {
  idle: 'default',
  downloading: 'primary',
  extracting: 'primary',
  complete: 'success',
  cancelled: 'warning',
  error: 'error',
}

/** Wakeword training depends on RIR + background-audio corpora (~13 GB). On a
 *  fresh Windows install those aren't there. This card lists each corpus,
 *  fetches missing ones from upstream URLs, and shows progress live via the
 *  wakeword_state WS event. */
export function CorporaCard({ status, onError, onAfterAction }: Props) {
  const api = useApi()
  const [busy, setBusy] = useState<string | null>(null)
  const corpora = status.corpora?.corpora ?? []
  const ready = status.corpora?.ready

  const handleAction = async (action: 'download' | 'cancel', body: { corpus: string }) => {
    setBusy(`${action}:${body.corpus}`)
    try {
      if (action === 'download') await api.startCorporaDownload(body.corpus)
      else await api.cancelCorporaDownload(body.corpus)
      await onAfterAction()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card variant="outlined">
      <CardHeader
        title="Training corpora"
        subheader={
          ready?.ready
            ? 'All ready — training can run.'
            : ready
              ? `Missing: ${ready.missing.join(', ')} — training blocked until present.`
              : 'Loading…'
        }
        action={
          <Button
            size="small"
            variant="outlined"
            startIcon={<DownloadIcon />}
            disabled={busy != null || corpora.every((c) => c.present)}
            onClick={() => handleAction('download', { corpus: 'all' })}
          >
            Download all
          </Button>
        }
        sx={{ pb: 0 }}
      />
      <CardContent>
        {corpora.length === 0 ? (
          <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
            Satellite not reachable.
          </Typography>
        ) : (
          <Stack sx={{ gap: 1 }}>
            {corpora.map((c) => (
              <CorpusRow key={c.name} corpus={c} busy={busy} onAction={handleAction} />
            ))}
          </Stack>
        )}
        <Divider sx={{ my: 2 }} />
        <ExtraPathsSection onError={onError} onAfterAction={onAfterAction} />
      </CardContent>
    </Card>
  )
}

/** User-managed list of extra background-audio dirs. Persisted satellite-side
 *  to wakeword_root/settings.json; survives restarts without env-var dance. */
function ExtraPathsSection({
  onError,
  onAfterAction,
}: {
  onError: (msg: string) => void
  onAfterAction: () => void | Promise<void>
}) {
  const api = useApi()
  const [paths, setPaths] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  // Initial fetch + reload after any save (so other tabs / curl edits show)
  const reload = async () => {
    try {
      const r = await api.getSettings()
      setPaths((r.extra_background_paths as string[] | undefined) ?? [])
    } catch (e) {
      // Satellite might not be up yet — silent on cold mount, retry on action
    }
  }
  useEffect(() => {
    reload()
  }, [])

  const save = async (next: string[]) => {
    setBusy(true)
    try {
      const r = await api.patchSettings({ extra_background_paths: next })
      setPaths((r.extra_background_paths as string[] | undefined) ?? [])
      setDraft('')
      await onAfterAction()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleAdd = () => {
    const trimmed = draft.trim()
    if (!trimmed || paths.includes(trimmed)) return
    save([...paths, trimmed])
  }
  const handleRemove = (p: string) => save(paths.filter((x) => x !== p))

  return (
    <Stack sx={{ gap: 1 }}>
      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
        Extra background dirs
      </Typography>
      <Typography variant="caption" color="text.disabled">
        Absolute paths to additional audio folders (e.g. Common Voice). Mixed
        into training augmentation alongside the corpora above. Persisted to{' '}
        <code>~/.jarvyz/satellites/wakeword-trainer/settings.json</code>.
      </Typography>
      {paths.length > 0 && (
        <Stack sx={{ gap: 0.5 }}>
          {paths.map((p) => (
            <Box
              key={p}
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                px: 1,
                py: 0.5,
                borderRadius: 1,
                border: 1,
                borderColor: 'divider',
              }}
            >
              <Typography
                variant="caption"
                sx={{
                  flex: 1,
                  minWidth: 0,
                  fontFamily: 'ui-monospace, monospace',
                  fontSize: '0.7rem',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {p}
              </Typography>
              <Tooltip title="Remove">
                <span>
                  <IconButton
                    size="small"
                    disabled={busy}
                    onClick={() => handleRemove(p)}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </Box>
          ))}
        </Stack>
      )}
      <Stack direction="row" sx={{ gap: 1 }}>
        <TextField
          size="small"
          fullWidth
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="C:\Users\…\extra_audio_dir"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && draft.trim() && !busy) handleAdd()
          }}
          disabled={busy}
          sx={{ '& input': { fontFamily: 'ui-monospace, monospace', fontSize: '0.75rem' } }}
        />
        <Button
          size="small"
          variant="outlined"
          startIcon={<AddIcon />}
          disabled={busy || !draft.trim()}
          onClick={handleAdd}
        >
          Add
        </Button>
      </Stack>
    </Stack>
  )
}

interface RowProps {
  corpus: CorpusView
  busy: string | null
  onAction: (path: string, body: { corpus: string }) => void | Promise<void>
}

function CorpusRow({ corpus: c, busy, onAction }: RowProps) {
  const downloading = c.phase === 'downloading' || c.phase === 'extracting'
  const total = c.bytes_total || c.expected_bytes
  const pct =
    downloading && total > 0
      ? Math.min(100, Math.round((c.bytes_done / total) * 100))
      : c.present
        ? 100
        : 0

  const label =
    c.phase === 'downloading'
      ? `downloading · ${fmtBytes(c.bytes_done)} / ${fmtBytes(total)}`
      : c.phase === 'extracting'
        ? `extracting · ${c.bytes_done}/${c.bytes_total} files`
        : c.phase === 'complete' || c.present
          ? `present · ${fmtBytes(c.bytes_on_disk)}`
          : c.phase === 'error'
            ? `error · ${c.error}`
            : c.phase === 'cancelled'
              ? 'cancelled'
              : `missing · expected ~${fmtBytes(c.expected_bytes)}`

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 0.5,
        p: 1,
        borderRadius: 1,
        border: 1,
        borderColor: c.present ? 'success.light' : downloading ? 'primary.light' : 'divider',
      }}
    >
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600, flex: 1, minWidth: 0 }}>
          {c.label}
        </Typography>
        <Chip
          size="small"
          label={c.phase}
          color={PHASE_COLOR[c.phase] ?? 'default'}
          variant={c.present || downloading ? 'filled' : 'outlined'}
          sx={{ height: 20, fontSize: '0.65rem' }}
        />
        {downloading ? (
          <Tooltip title="Cancel">
            <span>
              <Button
                size="small"
                color="warning"
                variant="outlined"
                startIcon={<StopIcon />}
                disabled={busy != null}
                onClick={() => onAction('cancel', { corpus: c.name })}
              >
                Stop
              </Button>
            </span>
          </Tooltip>
        ) : c.present ? null : (
          <Button
            size="small"
            variant="contained"
            startIcon={<DownloadIcon />}
            disabled={busy != null}
            onClick={() => onAction('download', { corpus: c.name })}
          >
            Download
          </Button>
        )}
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'ui-monospace, monospace' }}>
        {label}
      </Typography>
      <LinearProgress
        variant={downloading && total <= 0 ? 'indeterminate' : 'determinate'}
        value={pct}
        color={c.phase === 'error' ? 'error' : c.present ? 'success' : 'primary'}
        sx={{ height: 4, borderRadius: 2 }}
      />
      <Tooltip title={c.url}>
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.65rem',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {c.dest}
        </Typography>
      </Tooltip>
    </Box>
  )
}
