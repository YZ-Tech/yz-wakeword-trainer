import { useMemo, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useApi } from '../lib/api'

const LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'de', label: 'German' },
] as const

function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

export function NewModelDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (slug: string) => void
}) {
  const [phrase, setPhrase] = useState('hey ')
  const [language, setLanguage] = useState('en')
  const api = useApi()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const slug = useMemo(() => slugify(phrase), [phrase])
  const valid = phrase.trim().length >= 2 && slug.length >= 2

  const handleCreate = async () => {
    if (!valid) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.createModel({ phrase: phrase.trim(), language, slug })
      onCreated(r.slug)
      onClose()
      setPhrase('hey ')
      setLanguage('en')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New wake word</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Writes a model definition. Doesn't start training — hit
          "Start training" on the new model afterwards. The phrase is what
          Piper TTS will synthesize 50k variants of as positives.
        </Typography>
        <Stack sx={{ gap: 2, mt: 1 }}>
          <TextField
            label="Wake phrase"
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            placeholder="hey Aurora"
            fullWidth
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter' && valid && !busy) handleCreate()
            }}
            helperText={
              slug
                ? `slug: ${slug} · dataset: ~/.jarvyz/wakeword/runs/${slug}/ · onnx: ${slug}.onnx`
                : 'enter a phrase'
            }
          />
          <TextField
            select
            label="Language"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            helperText="Piper voice set. English has the widest speaker pool; other languages can be added if Piper supports them."
          >
            {LANGUAGES.map((l) => (
              <MenuItem key={l.value} value={l.value}>
                {l.label}
              </MenuItem>
            ))}
          </TextField>
          {error && (
            <Typography variant="caption" color="error.main">
              {error}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleCreate}
          disabled={!valid || busy}
        >
          {busy ? 'Creating…' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
