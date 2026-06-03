import { Box, Chip, Stack, Typography } from '@mui/material'
import type { ModelView } from '../types'

// Default clip-gen targets baked in. Matches the satellite defaults at
// clip_gen.py `_resolve_counts` (50000 train / 5000 test). Hardcoded
// here so the chip can show progress without an extra backend roundtrip;
// if the user starts overriding via JWT_CLIPS_* env vars, surface the
// real targets through /status instead.
const TARGET_TRAIN = 50000
const TARGET_TEST = 5000

/** 3rd status panel — per-model dataset counts. Four chips: one per
 *  bucket (pos·train, pos·test, neg·train, neg·test), each showing
 *  count/target so progress is obvious. */
export function DatasetPanel({ dataset }: { dataset: ModelView['dataset'] }) {
  const buckets = [
    { label: 'pos·train', count: dataset.positive_train ?? 0, target: TARGET_TRAIN },
    { label: 'pos·test',  count: dataset.positive_test ?? 0,  target: TARGET_TEST },
    { label: 'neg·train', count: dataset.negative_train ?? 0, target: TARGET_TRAIN },
    { label: 'neg·test',  count: dataset.negative_test ?? 0,  target: TARGET_TEST },
  ]
  const empty = buckets.every((b) => b.count === 0)
  return (
    <Box
      sx={{
        flex: 1.4,
        p: 1.5,
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
      }}
    >
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        Dataset (this model)
      </Typography>
      {empty ? (
        <Typography variant="caption" color="text.disabled">
          No dataset generated yet — click Start training.
        </Typography>
      ) : (
        <Stack direction="row" sx={{ gap: 0.5, flexWrap: 'wrap' }}>
          {buckets.map((b) => {
            const full = b.count >= b.target
            const targetShort =
              b.target >= 1000 ? `${b.target / 1000}k` : `${b.target}`
            return (
              <Chip
                key={b.label}
                size="small"
                label={`${b.label} ${b.count.toLocaleString()}/${targetShort}`}
                color={full ? 'success' : 'default'}
                variant={full ? 'filled' : 'outlined'}
                sx={{ height: 20, fontSize: '0.65rem' }}
              />
            )
          })}
        </Stack>
      )}
    </Box>
  )
}
