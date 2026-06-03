import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import StopIcon from '@mui/icons-material/Stop'
import { fmtAgo, fmtBytes } from '../lib/fmt'
import { useCallback } from 'react'
import { useApi } from '../lib/api'
import { useLogTail } from '../hooks/useLogTail'
import type { BackgroundItem } from '../types'
import { Dot } from './Dot'
import { LogPanel } from './LogPanel'

/** Full room ambience manage UI — opened from the RoomPanel click. */
export function RoomManageDialog({
  open,
  onClose,
  items,
  running,
  busy,
  onRecordClick,
  onStop,
  onDelete,
}: {
  open: boolean
  onClose: () => void
  items: BackgroundItem[]
  running: boolean
  busy: string | null
  onRecordClick: () => void
  onStop: () => void
  onDelete: (name: string) => void
}) {
  // Live log via polling — see useLogTail's "why polling" docstring.
  // Active only while the dialog is open AND a recording is in progress.
  const api = useApi()
  const fetchLog = useCallback(() => api.getRoomRecordLog({ tail: 40 }), [api])
  const log = useLogTail(fetchLog, open && running)

  const totalBytes = items.reduce((acc, it) => acc + it.size_bytes, 0)

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
          <Dot on={running} color="error.main" />
          Room ambience
          {running && (
            <Chip
              size="small"
              label="recording"
              color="error"
              icon={<FiberManualRecordIcon sx={{ fontSize: 12 }} />}
              sx={{ height: 20 }}
            />
          )}
        </Stack>
      </DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          {items.length} clip{items.length === 1 ? '' : 's'} · {fmtBytes(totalBytes)} total.
          Heavy-weighted in augmentation so the model learns to ignore THIS room's noise floor.
        </Typography>

        {running && (
          <Box sx={{ mb: 1.5 }}>
            <LinearProgress />
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
              Stay in the room behaving normally — typing, breathing, fans, mouse clicks.
              Don't speak.
            </Typography>
          </Box>
        )}

        {items.length === 0 ? (
          <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
            No recordings yet.
          </Typography>
        ) : (
          <Stack sx={{ gap: 0.25, maxHeight: 280, overflow: 'auto' }}>
            {items.map((it) => (
              <Stack
                key={it.name}
                direction="row"
                sx={{ alignItems: 'center', gap: 1, py: 0.25 }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    fontFamily: 'ui-monospace, monospace',
                    fontSize: '0.78rem',
                  }}
                  noWrap
                >
                  {it.name}
                </Typography>
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ minWidth: 60, textAlign: 'right' }}
                >
                  {fmtBytes(it.size_bytes)}
                </Typography>
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ minWidth: 70, textAlign: 'right' }}
                >
                  {fmtAgo(it.mtime)}
                </Typography>
                <Tooltip title="Delete">
                  <IconButton size="small" onClick={() => onDelete(it.name)}>
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            ))}
          </Stack>
        )}

        {(running || log) && (
          <Box sx={{ mt: 1.5 }}>
            <LogPanel text={log} height={running ? 160 : 100} />
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {!running ? (
          <Button
            variant="contained"
            color="error"
            startIcon={<FiberManualRecordIcon />}
            onClick={onRecordClick}
            disabled={busy !== null}
          >
            Record
          </Button>
        ) : (
          <Button
            variant="outlined"
            startIcon={<StopIcon />}
            onClick={onStop}
            disabled={busy !== null}
          >
            {busy === 'stop' ? 'Stopping…' : 'Stop'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}
