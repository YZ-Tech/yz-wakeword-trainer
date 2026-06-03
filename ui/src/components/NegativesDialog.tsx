import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import { useApi } from '../lib/api'

/** Custom negative phrases editor — TTS-synthesized as hard negatives at
 *  training time. State lives here; loaded lazily when the dialog opens. */
export function NegativesDialog({
  open,
  onClose,
  onError,
}: {
  open: boolean
  onClose: () => void
  onError: (msg: string) => void
}) {
  const api = useApi()
  const [phrases, setPhrases] = useState<string[]>([])
  const [draft, setDraft] = useState<string>('')
  const [loaded, setLoaded] = useState(false)

  // Load once per open. Reset on close.
  if (open && !loaded) {
    setLoaded(true)
    api
      .getGlobalNegatives()
      .then((phrases) => setPhrases(phrases))
      .catch((e) => onError(e instanceof Error ? e.message : String(e)))
  } else if (!open && loaded) {
    setLoaded(false)
    setDraft('')
  }

  const save = async (next: string[]) => {
    try {
      await api.setGlobalNegatives(next)
      setPhrases(next)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  const add = async () => {
    const v = draft.trim()
    if (!v) return
    if (phrases.includes(v)) {
      setDraft('')
      return
    }
    await save([...phrases, v])
    setDraft('')
  }

  const remove = (idx: number) => save(phrases.filter((_, i) => i !== idx))

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Custom negative phrases</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          TTS-synthesized as hard negatives at training time. Heavy on "hey X" near-misses,
          loom rhymes, and chatter the household actually says.
        </Typography>
        <Stack direction="row" sx={{ gap: 1, mb: 1 }}>
          <TextField
            size="small"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="hey laura"
            fullWidth
            onKeyDown={(e) => {
              if (e.key === 'Enter') add()
            }}
          />
          <IconButton size="small" onClick={add} disabled={!draft.trim()}>
            <AddIcon />
          </IconButton>
        </Stack>
        <Divider sx={{ mb: 1 }} />
        <Stack sx={{ gap: 0.25, maxHeight: 320, overflow: 'auto' }}>
          {phrases.length === 0 ? (
            <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
              (no custom negatives)
            </Typography>
          ) : (
            phrases.map((p, i) => (
              <Stack
                key={`${i}-${p}`}
                direction="row"
                sx={{ alignItems: 'center', gap: 1, py: 0.25 }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    fontFamily: 'ui-monospace, monospace',
                    fontSize: '0.78rem',
                  }}
                >
                  {p}
                </Typography>
                <IconButton size="small" onClick={() => remove(i)}>
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Stack>
            ))
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
