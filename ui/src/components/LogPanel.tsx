import { useEffect, useRef } from 'react'
import { Box } from '@mui/material'

/** Monospace log panel — used by trainer + record + spike log displays.
 *  Pins to bottom on new content (mirrors `tail -f` behavior). */
export function LogPanel({
  text,
  height = 220,
  testid,
}: { text: string; height?: number; testid?: string }) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [text])
  return (
    <Box
      ref={ref}
      data-testid={testid}
      sx={{
        bgcolor: 'background.default',
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        p: 1,
        fontFamily: 'ui-monospace, monospace',
        fontSize: '0.72rem',
        color: 'text.secondary',
        whiteSpace: 'pre-wrap',
        height,
        overflow: 'auto',
      }}
    >
      {text || <span style={{ opacity: 0.5 }}>(no output yet)</span>}
    </Box>
  )
}
