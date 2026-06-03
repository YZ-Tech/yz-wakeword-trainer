import { Box, Chip, Typography } from '@mui/material'
import { fmtAgo, fmtBytes } from '../lib/fmt'
import type { ModelView } from '../types'

/** 2nd status panel — the latest <slug>.onnx the trainer wrote in WSL,
 *  with a "newer than deployed" chip when a Deploy button click would
 *  move forward in time. */
export function WslOutputPanel({
  dataset,
  deployed,
}: {
  dataset: ModelView['dataset']
  deployed: ModelView['deployed']
}) {
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
        Latest WSL output
      </Typography>
      {(dataset.wsl_onnx_size ?? 0) > 0 ? (
        <>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {(deployed.path.split(/[\\/]/).pop() ?? '').replace(/\.onnx$/, '')}.onnx (WSL)
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {fmtBytes(dataset.wsl_onnx_size ?? 0)} · {fmtAgo(dataset.wsl_onnx_mtime ?? 0)}
          </Typography>
          {(dataset.wsl_onnx_mtime ?? 0) > (deployed.mtime ?? 0) && (
            <Chip
              size="small"
              label="newer than deployed"
              color="warning"
              sx={{ mt: 0.5, height: 18, fontSize: '0.65rem' }}
            />
          )}
        </>
      ) : (
        <Typography variant="caption" color="text.disabled">
          No build output yet.
        </Typography>
      )}
    </Box>
  )
}
