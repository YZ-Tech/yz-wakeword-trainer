import { useEffect, useState } from 'react'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import CloseIcon from '@mui/icons-material/Close'
import DeleteIcon from '@mui/icons-material/Delete'
import DownloadIcon from '@mui/icons-material/Download'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutlineRounded'
import FolderOpenIcon from '@mui/icons-material/FolderOpen'
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked'
import StopIcon from '@mui/icons-material/Stop'
import { useApi } from '../lib/api'
import { fmtBytes } from '../lib/fmt'
import type { CorpusView, WakewordStatus } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  status: WakewordStatus
  onError: (msg: string) => void
  onAfterAction: () => void | Promise<void>
  /** Snapshot fetch error, if any. Lets us tell "still loading" apart
   *  from "satellite unreachable" instead of conflating both as empty. */
  error?: string | null
}

/** Setup-mode dialog wrapping the corpora download / extras flow.
 *  Auto-opened by WakeWordTab when corpora.ready === false on mount;
 *  user-toggled via the gear icon afterwards. Does NOT auto-close on
 *  ready — explicit Close button only. */
export function CorporaSetupDialog({
  open,
  onClose,
  status,
  onError,
  onAfterAction,
  error,
}: Props) {
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

  // Header status: compact chip variant instead of paragraph text.
  const allComplete = corpora.length > 0 && corpora.every((c) => c.present)
  const missingNames = ready?.missing ?? []

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pr: 6, pb: 1 }}>
        <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
          <Box sx={{ flex: 1 }}>Training audio</Box>
          {ready &&
            (ready.ready ? (
              <Chip
                size="small"
                icon={<CheckCircleIcon fontSize="small" />}
                label="ready"
                color="success"
                variant="outlined"
              />
            ) : (
              <Chip
                size="small"
                icon={<ErrorOutlineIcon fontSize="small" />}
                label={`${missingNames.length} missing`}
                color="warning"
                variant="outlined"
              />
            ))}
        </Stack>
        <IconButton
          aria-label="close"
          onClick={onClose}
          sx={{ position: 'absolute', right: 8, top: 8 }}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers sx={{ pt: 1.5 }}>
        {!allComplete && corpora.length > 0 && (
          <Stack direction="row" sx={{ alignItems: 'center', mb: 1.5, gap: 1 }}>
            <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
              {missingNames.length > 0
                ? `Missing: ${missingNames.join(', ')}`
                : 'Setup in progress…'}
            </Typography>
            <Button
              size="small"
              variant="contained"
              startIcon={<DownloadIcon />}
              disabled={busy != null}
              onClick={() => handleAction('download', { corpus: 'all' })}
            >
              Download all
            </Button>
          </Stack>
        )}

        {status.corpora === undefined ? (
          error ? (
            <Typography variant="caption" color="error.main" sx={{ fontStyle: 'italic' }}>
              Satellite not reachable — {error}
            </Typography>
          ) : (
            <Stack
              direction="row"
              sx={{ alignItems: 'center', gap: 1, py: 1, color: 'text.secondary' }}
            >
              <CircularProgress size={18} />
              <Typography variant="caption">Loading corpora…</Typography>
            </Stack>
          )
        ) : corpora.length === 0 ? (
          <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
            No corpora configured.
          </Typography>
        ) : (
          <Stack sx={{ gap: 0.75 }}>
            {corpora.map((c) => (
              <CorpusRow key={c.name} corpus={c} busy={busy} onAction={handleAction} />
            ))}
          </Stack>
        )}

        <Divider sx={{ my: 2.5, borderStyle: 'dashed' }} />
        <ExtraPathsSection onError={onError} onAfterAction={onAfterAction} />
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 1.5 }}>
        <Button onClick={onClose} variant={allComplete ? 'contained' : 'outlined'}>
          {allComplete ? 'Done' : 'Close'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

/** Per-corpus row — three visual modes by phase:
 *    complete  → compact single line (icon · name · size · path-on-hover)
 *    downloading/extracting → full progress card with bar
 *    missing/error/cancelled → call-to-action card with Download button
 */
function CorpusRow({
  corpus: c,
  busy,
  onAction,
}: {
  corpus: CorpusView
  busy: string | null
  onAction: (path: string, body: { corpus: string }) => void | Promise<void>
}) {
  const downloading = c.phase === 'downloading' || c.phase === 'extracting'
  const failed = c.phase === 'error'

  // === COMPLETE: collapse to a single muted row ===
  if (c.present && !downloading) {
    return (
      <Tooltip title={c.dest} placement="left">
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            px: 1.25,
            py: 0.5,
            borderRadius: 1,
            border: 1,
            borderColor: 'divider',
            bgcolor: 'action.hover',
          }}
        >
          <CheckCircleIcon sx={{ color: 'success.main', fontSize: 16 }} />
          <Typography variant="body2" sx={{ flex: 1, minWidth: 0 }}>
            {c.label}
          </Typography>
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.7rem' }}
          >
            {fmtBytes(c.bytes_on_disk)}
          </Typography>
        </Box>
      </Tooltip>
    )
  }

  // === ACTIVE OR PENDING: full layout ===
  const total = c.bytes_total || c.expected_bytes
  const pct =
    downloading && total > 0
      ? Math.min(100, Math.round((c.bytes_done / total) * 100))
      : 0
  const statusLine =
    c.phase === 'downloading'
      ? `${fmtBytes(c.bytes_done)} / ${fmtBytes(total)}`
      : c.phase === 'extracting'
        ? `extracting · ${c.bytes_done}/${c.bytes_total} files`
        : c.phase === 'error'
          ? c.error || 'download failed'
          : c.phase === 'cancelled'
            ? 'cancelled'
            : `expected ~${fmtBytes(c.expected_bytes)}`

  const borderColor = failed
    ? 'error.main'
    : downloading
      ? 'primary.main'
      : c.phase === 'cancelled'
        ? 'warning.main'
        : 'divider'

  const Icon = failed ? ErrorOutlineIcon : RadioButtonUncheckedIcon
  const iconColor = failed ? 'error.main' : 'text.disabled'

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 0.75,
        px: 1.25,
        py: 1,
        borderRadius: 1,
        border: 1,
        borderColor,
      }}
    >
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
        <Icon sx={{ color: iconColor, fontSize: 16 }} />
        <Typography variant="body2" sx={{ fontWeight: 500, flex: 1, minWidth: 0 }}>
          {c.label}
        </Typography>
        {downloading ? (
          <Button
            size="small"
            color="warning"
            variant="text"
            startIcon={<StopIcon />}
            disabled={busy != null}
            onClick={() => onAction('cancel', { corpus: c.name })}
          >
            Stop
          </Button>
        ) : (
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

      {downloading && (
        <LinearProgress
          variant={total <= 0 ? 'indeterminate' : 'determinate'}
          value={pct}
          color="primary"
          sx={{ height: 4, borderRadius: 2 }}
        />
      )}

      <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
        <Typography
          variant="caption"
          color={failed ? 'error.main' : 'text.secondary'}
          sx={{
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.7rem',
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {statusLine}
        </Typography>
        <Tooltip title={c.dest}>
          <FolderOpenIcon sx={{ color: 'text.disabled', fontSize: 14 }} />
        </Tooltip>
      </Stack>
    </Box>
  )
}

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

  const reload = async () => {
    try {
      const r = await api.getSettings()
      setPaths((r.extra_background_paths as string[] | undefined) ?? [])
    } catch {
      /* satellite cold — silent */
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
    <Stack sx={{ gap: 0.75 }}>
      <Stack direction="row" sx={{ alignItems: 'baseline', gap: 1 }}>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          Extra background dirs
        </Typography>
        <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.65rem' }}>
          {paths.length} active
        </Typography>
      </Stack>
      <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.7rem' }}>
        Additional audio folders mixed into training augmentation. Persisted
        to <code>~/.jarvyz/satellites/wakeword-trainer/settings.json</code>.
      </Typography>
      {paths.length > 0 && (
        <Stack sx={{ gap: 0.25 }}>
          {paths.map((p) => (
            <Box
              key={p}
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                px: 1.25,
                py: 0.25,
                borderRadius: 1,
                border: 1,
                borderColor: 'divider',
                bgcolor: 'action.hover',
              }}
            >
              <FolderOpenIcon sx={{ color: 'text.disabled', fontSize: 14 }} />
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
                  <IconButton size="small" disabled={busy} onClick={() => handleRemove(p)}>
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </Box>
          ))}
        </Stack>
      )}
      <Stack direction="row" sx={{ gap: 1, mt: 0.5 }}>
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
