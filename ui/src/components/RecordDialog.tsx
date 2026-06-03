import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Slider,
  Typography,
} from '@mui/material'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'

/** Duration picker — opened from RoomManageDialog's Record button. */
export function RecordDialog({
  open,
  onClose,
  onStart,
  busy,
}: {
  open: boolean
  onClose: () => void
  onStart: (minutes: number) => Promise<void>
  busy: string | null
}) {
  const [minutes, setMinutes] = useState(10)

  const handleStart = async () => {
    await onStart(minutes)
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Record room audio</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Captures from the configured JarvYZ mic at 16 kHz mono, split into 30s chunks.
          Heavy-weighted in augmentation so the model learns to ignore THIS room.
        </Typography>
        <Typography variant="body2" sx={{ mb: 1 }}>
          Duration: <strong>{minutes} min</strong>
        </Typography>
        <Slider
          value={minutes}
          onChange={(_, v) => setMinutes(Array.isArray(v) ? v[0] : v)}
          min={1}
          max={30}
          marks={[
            { value: 1, label: '1' },
            { value: 10, label: '10' },
            { value: 20, label: '20' },
            { value: 30, label: '30' },
          ]}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="error"
          startIcon={<FiberManualRecordIcon />}
          onClick={handleStart}
          disabled={busy !== null}
        >
          {busy === 'start' ? 'Starting…' : `Record ${minutes} min`}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
