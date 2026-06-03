import { Box, Typography } from '@mui/material'
import { fmtAgo, fmtBytes } from '../lib/fmt'
import type { ModelView } from '../types'

/** 1st status panel — the file pipeline/wake.py reads at boot. */
export function DeployedPanel({ deployed }: { deployed: ModelView['deployed'] }) {
  const filename = deployed.path.split(/[\\/]/).pop() ?? deployed.path
  return (
    <Box
      sx={{
        flex: 1,
        p: 1.5,
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
      }}
    >
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        Deployed
      </Typography>
      {deployed.exists ? (
        <>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {filename}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {fmtBytes(deployed.size_bytes ?? 0)} · {fmtAgo(deployed.mtime ?? 0)}
          </Typography>
          <Typography
            variant="caption"
            sx={{
              display: 'block',
              fontFamily: 'ui-monospace, monospace',
              fontSize: '0.65rem',
              color: 'text.disabled',
              mt: 0.5,
            }}
            noWrap
          >
            {deployed.path}
          </Typography>
        </>
      ) : (
        <Typography variant="caption" color="text.disabled">
          Not deployed yet.
        </Typography>
      )}
    </Box>
  )
}
