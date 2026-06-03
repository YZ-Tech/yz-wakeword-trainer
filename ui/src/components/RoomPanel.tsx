import { Box, Chip, Stack, Typography } from '@mui/material'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import { fmtAgo, fmtBytes } from '../lib/fmt'
import type { BackgroundItem } from '../types'
import { Dot } from './Dot'

/** 4th status panel — room ambience clips. Clickable; opens the manage
 *  dialog. Recording IS part of the training pipeline (the clips are
 *  used as background noise during augmentation), so it lives here as
 *  a peer status panel rather than as a separate card. */
export function RoomPanel({
  items,
  recording,
  onClick,
}: {
  items: BackgroundItem[]
  recording: boolean
  onClick: () => void
}) {
  return (
    <Box
      onClick={onClick}
      sx={{
        flex: 1,
        p: 1.5,
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        cursor: 'pointer',
        transition: 'background-color .12s ease',
        '&:hover': { bgcolor: 'action.hover' },
      }}
    >
      <Stack direction="row" sx={{ alignItems: 'center', gap: 0.75, mb: 0.5 }}>
        <Dot on={recording} color="error.main" />
        <Typography variant="caption" color="text.secondary">
          Room ambience
        </Typography>
      </Stack>
      {items.length === 0 ? (
        <Typography variant="caption" color="text.disabled">
          No recordings yet — click to record.
        </Typography>
      ) : (
        <>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {items.length} clip{items.length === 1 ? '' : 's'}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {fmtBytes(items.reduce((a, it) => a + it.size_bytes, 0))} ·{' '}
            newest {fmtAgo(Math.max(...items.map((it) => it.mtime)))}
          </Typography>
        </>
      )}
      {recording && (
        <Chip
          size="small"
          label="recording"
          color="error"
          icon={<FiberManualRecordIcon sx={{ fontSize: 12 }} />}
          sx={{ mt: 0.5, height: 18, fontSize: '0.65rem' }}
        />
      )}
    </Box>
  )
}
