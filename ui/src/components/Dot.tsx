import { Box } from '@mui/material'

/** Tiny status dot used in card headers + sub-panels. */
export function Dot({ on, color = 'success.main' }: { on: boolean; color?: string }) {
  return (
    <Box
      sx={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        bgcolor: on ? color : 'text.disabled',
        flex: '0 0 auto',
      }}
    />
  )
}
