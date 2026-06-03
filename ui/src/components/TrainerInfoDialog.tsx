import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Stack,
  Typography,
} from '@mui/material'
import type { ModelView } from '../types'

const mono = { fontFamily: 'ui-monospace, monospace' } as const

export function TrainerInfoDialog({
  open,
  onClose,
  model,
}: {
  open: boolean
  onClose: () => void
  model?: ModelView | null
}) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>How wake-word training works</DialogTitle>
      <DialogContent>
        {model && (
          <>
            <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
              This model
            </Typography>
            <Stack sx={{ gap: 0.25, mb: 1.5 }}>
              <KV k="phrase" v={model.phrase} />
              <KV k="slug" v={model.slug} mono />
              <KV k="language" v={model.language} />
              <KV
                k="dataset"
                v={`~/.jarvyz/wakeword/runs/${model.slug}/`}
                mono
              />
              <KV
                k="trained .onnx"
                v={`~/.jarvyz/wakeword/runs/${model.slug}.onnx`}
                mono
              />
              <KV
                k="deployed .onnx"
                v={model.deployed.path || '(not deployed)'}
                mono
              />
              <KV k="negatives" v={`${(model.negatives ?? []).length} adversarial phrases`} />
            </Stack>
            <Divider sx={{ mb: 1.5 }} />
          </>
        )}

        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
          What "Start training" does
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 1 }}>
          End-to-end: teach the assistant to recognize your wake phrase. No
          manual steps after clicking.
        </Typography>
        <Box component="ol" sx={{ pl: 2.5, mb: 1.5, '& li': { mb: 0.5 } }}>
          <Typography component="li" variant="body2" sx={{ color: 'text.secondary' }}>
            Piper TTS synthesizes ~50,000 different ways someone might say
            the phrase (rotating voices, speeds, intonations) as positive
            training clips.
          </Typography>
          <Typography component="li" variant="body2" sx={{ color: 'text.secondary' }}>
            Another ~50,000 negative clips synthesized from the adversarial
            phrase list — rhymes, near-misses, household chatter — teach
            the model what is <em>not</em> the wake word.
          </Typography>
          <Typography component="li" variant="body2" sx={{ color: 'text.secondary' }}>
            Every clip gets mixed with a random room impulse + background
            audio (music, speech, your recorded room ambience) so the
            model learns to ignore reverb and noise.
          </Typography>
          <Typography component="li" variant="body2" sx={{ color: 'text.secondary' }}>
            A small neural network is trained on all of it. Roughly
            10-15 min for the training step on a 4090 once features are
            cached; closer to 30 min for a fresh slug.
          </Typography>
          <Typography component="li" variant="body2" sx={{ color: 'text.secondary' }}>
            The new <code style={mono}>{model?.slug ?? '<slug>'}.onnx</code>{' '}
            lands in <code style={mono}>~/.jarvyz/wakeword/runs/</code>.
          </Typography>
        </Box>
        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 2 }}>
          The button does <strong>not</strong> deploy. After training, hit{' '}
          <strong>Deploy → Windows</strong> to copy the new <code style={mono}>.onnx</code>{' '}
          into <code style={mono}>~/.jarvyz/openwakeword/</code> where the
          live wake stack reads it. JarvYZ restart needed for the new model
          to take effect. Resumable — if the dataset is already generated,
          the next click picks up at the next missing step.
        </Typography>

        <Divider sx={{ mb: 1.5 }} />

        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
          Where this runs
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 1 }}>
          The training pipeline lives in the{' '}
          <code style={mono}>wakeword-trainer</code> satellite at{' '}
          <code style={mono}>satellites/yz-wakeword-trainer/</code>. JarvYZ
          auto-spawns it as a local HTTP service on{' '}
          <code style={mono}>127.0.0.1:9001</code>.
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Fully cross-platform: runs natively on Windows or Linux with
          identical layout (<code style={mono}>~/.jarvyz/wakeword/</code>{' '}
          on either OS). GPU acceleration via <code style={mono}>onnxruntime-gpu</code>{' '}
          + PyTorch CUDA when available.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Got it</Button>
      </DialogActions>
    </Dialog>
  )
}

/** Compact key-value row for the "This model" detail block. */
function KV({ k, v, mono: isMono }: { k: string; v: string; mono?: boolean }) {
  return (
    <Stack direction="row" sx={{ gap: 1, alignItems: 'baseline' }}>
      <Typography
        variant="caption"
        sx={{ color: 'text.disabled', width: 96, flexShrink: 0 }}
      >
        {k}
      </Typography>
      <Typography
        variant="caption"
        sx={{
          color: 'text.primary',
          flex: 1,
          minWidth: 0,
          fontFamily: isMono ? 'ui-monospace, monospace' : undefined,
          fontSize: isMono ? '0.7rem' : undefined,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {v}
      </Typography>
    </Stack>
  )
}
