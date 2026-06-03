import { useEffect, useState } from 'react'
import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  Typography,
} from '@mui/material'
import DeleteForeverIcon from '@mui/icons-material/DeleteForever'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'

export type PreflightState = 'fresh' | 'clean' | 'partial' | 'full_no_onnx' | 'hash_drift'

export interface TrainPreflight {
  slug: string
  state: PreflightState
  would_wipe: boolean
  cur_hash: string
  last_hash: string | null
  pos_train_clips: number
  pos_test_clips: number
  neg_train_clips: number
  neg_test_clips: number
  target_train: number
  target_test: number
  npy_features: number
  onnx_size: number
  /** Files that failed load-validation (validate-tail from newest mtime).
   *  Continue will delete + regenerate these. Empty list = all artifacts
   *  load cleanly. */
  corrupt_files: string[]
  /** Backend kill-switch — when false, the satellite refuses to delete
   *  files regardless of `wipe_stale=true` on the API. Toggled only via
   *  JWT_WIPE_ENABLED env var + satellite restart. */
  wipe_enabled: boolean
}

/** Shown when the slug's on-disk state requires a decision before training.
 *  Smart-Start fires directly for `fresh` and `clean` — this dialog never
 *  opens in those cases. */
export function TrainPreflightDialog({
  open,
  preflight,
  onCancel,
  onContinue,
  onClearAndStart,
  busy,
}: {
  open: boolean
  preflight: TrainPreflight | null
  onCancel: () => void
  onContinue: () => void
  onClearAndStart: () => void
  busy: boolean
}) {
  const [deleteUnlocked, setDeleteUnlocked] = useState(false)

  // Re-lock every time the dialog reopens — never carry permission across
  // sessions. User must explicitly re-confirm each time.
  useEffect(() => {
    if (open) setDeleteUnlocked(false)
  }, [open])

  if (!preflight) return null

  const totalClips =
    preflight.pos_train_clips +
    preflight.pos_test_clips +
    preflight.neg_train_clips +
    preflight.neg_test_clips

  const fmtBytes = (n: number) =>
    n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${(n / 1000).toFixed(1)} kB`

  const { title, continueLabel, continueBlurb, clearBlurb } = describeState(preflight)

  return (
    <Dialog open={open} onClose={busy ? undefined : onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mb: 2 }}>
          {preflight.slug}
        </Typography>

        <Stack
          direction="row"
          sx={{
            gap: 3,
            p: 2,
            mb: 2,
            bgcolor: 'background.default',
            borderRadius: 1,
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.85rem',
            flexWrap: 'wrap',
          }}
        >
          <CountCell
            label="pos_train"
            value={preflight.pos_train_clips}
            target={preflight.target_train}
          />
          <CountCell
            label="pos_test"
            value={preflight.pos_test_clips}
            target={preflight.target_test}
          />
          <CountCell
            label="neg_train"
            value={preflight.neg_train_clips}
            target={preflight.target_train}
          />
          <CountCell
            label="neg_test"
            value={preflight.neg_test_clips}
            target={preflight.target_test}
          />
          <Stack>
            <Typography variant="caption" color="text.disabled">
              .npy feats
            </Typography>
            <Typography>{preflight.npy_features}</Typography>
          </Stack>
          <Stack>
            <Typography variant="caption" color="text.disabled">
              .onnx
            </Typography>
            <Typography>
              {preflight.onnx_size > 0 ? fmtBytes(preflight.onnx_size) : '—'}
            </Typography>
          </Stack>
        </Stack>

        {preflight.state === 'hash_drift' && preflight.last_hash && (
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mb: 2 }}>
            negatives hash: {preflight.last_hash} → {preflight.cur_hash}
          </Typography>
        )}

        {preflight.corrupt_files.length > 0 && (
          <Typography variant="caption" color="warning.main" sx={{ display: 'block', mb: 2 }}>
            {preflight.corrupt_files.length} corrupt file
            {preflight.corrupt_files.length === 1 ? '' : 's'} detected (
            {preflight.corrupt_files.slice(0, 3).join(', ')}
            {preflight.corrupt_files.length > 3
              ? `, +${preflight.corrupt_files.length - 3} more`
              : ''}
            ). Continue will delete and regenerate {preflight.corrupt_files.length === 1
              ? 'it'
              : 'them'}.
          </Typography>
        )}

        <Typography variant="body2" sx={{ mb: 1 }}>
          <strong>Continue</strong> — {continueBlurb}
        </Typography>
        <Typography variant="body2" sx={{ mb: 2 }}>
          <strong>Clear & start from new</strong> — {clearBlurb} (deletes{' '}
          {totalClips.toLocaleString()} wavs
          {preflight.npy_features > 0 ? ` + ${preflight.npy_features} feature files` : ''}
          {preflight.onnx_size > 0 ? ' + the .onnx' : ''}).
        </Typography>

        {preflight.wipe_enabled ? (
          <FormControlLabel
            control={
              <Checkbox
                checked={deleteUnlocked}
                onChange={(e) => setDeleteUnlocked(e.target.checked)}
                color="error"
                disabled={busy}
              />
            }
            label={
              <Typography variant="caption" color="error.main">
                I want to permanently delete {totalClips.toLocaleString()} wavs
                {preflight.npy_features > 0 ? ` + ${preflight.npy_features} feature files` : ''}
                {preflight.onnx_size > 0 ? ' + the .onnx' : ''}.
              </Typography>
            }
          />
        ) : (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ display: 'block', fontStyle: 'italic' }}
          >
            Wipes are disabled by the satellite kill-switch. Set
            <code> JWT_WIPE_ENABLED=1 </code> in the environment and restart the
            wakeword-trainer satellite to allow deletion. Until then the red button
            below is inert.
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          startIcon={<PlayArrowIcon />}
          onClick={onContinue}
          disabled={busy}
        >
          {busy ? 'Starting…' : continueLabel}
        </Button>
        <Button
          variant="outlined"
          color="error"
          startIcon={<DeleteForeverIcon />}
          onClick={onClearAndStart}
          disabled={busy || !preflight.wipe_enabled || !deleteUnlocked}
        >
          Clear & start from new
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function CountCell({ label, value, target }: { label: string; value: number; target: number }) {
  const full = value >= target
  return (
    <Stack>
      <Typography variant="caption" color="text.disabled">
        {label}
      </Typography>
      <Typography color={full ? 'text.primary' : 'warning.main'}>
        {value.toLocaleString()}
        <Typography component="span" variant="caption" color="text.disabled">
          {' '}
          / {target.toLocaleString()}
        </Typography>
      </Typography>
    </Stack>
  )
}

function describeState(p: TrainPreflight): {
  title: string
  continueLabel: string
  continueBlurb: string
  clearBlurb: string
} {
  switch (p.state) {
    case 'partial':
      return {
        title: 'Resume partial training data?',
        continueLabel: 'Continue (fill gaps)',
        continueBlurb:
          'fill the missing clips, then run features + training. Cheaper than starting over.',
        clearBlurb: 'wipe everything and regenerate from scratch',
      }
    case 'full_no_onnx':
      return {
        title: 'Retry training?',
        continueLabel: 'Continue (retry training)',
        continueBlurb:
          'skip clip_gen + features (already done) and re-run the training step only. Fast.',
        clearBlurb: 'wipe clips + features and regenerate everything',
      }
    case 'hash_drift':
      return {
        title: 'Negatives changed — what now?',
        continueLabel: 'Continue (existing clips)',
        continueBlurb:
          "use the wavs already on disk. The dataset-hash sidecar says they were generated under the OLD negatives list, so any gaps clip_gen fills now will be a MIX (old surviving wavs + new fill wavs). Training proceeds but the negative space is muddled. OK for a quick A/B; not OK for a production run.",
        clearBlurb:
          'wipe everything and regenerate under the current negatives list (clean, single-hash dataset)',
      }
    // fresh / clean shouldn't open the dialog at all
    default:
      return {
        title: 'Start training?',
        continueLabel: 'Start',
        continueBlurb: 'start training with current on-disk state.',
        clearBlurb: 'wipe everything and regenerate from scratch',
      }
  }
}
