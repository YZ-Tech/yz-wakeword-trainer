import { useEffect, useState } from 'react'
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import { useApi } from '../lib/api'

interface Props {
  open: boolean
  onClose: () => void
  slug: string
  phrase: string
  onSaved: () => void | Promise<void>
}

/** Edit a slug's adversarial-negative phrase list. Saves to the model
 *  meta JSON. The next /train run smart-invalidates stale negative
 *  clips so the new phrases actually take effect. */
export function EditNegativesDialog({ open, onClose, slug, phrase, onSaved }: Props) {
  const api = useApi()
  const [items, setItems] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [origJoined, setOrigJoined] = useState('')

  useEffect(() => {
    if (!open || !slug) return
    setError(null)
    setBusy(true)
    api
      .getModelNegatives(slug)
      .then((phrases) => {
        setItems(phrases)
        setOrigJoined(phrases.join('\n'))
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false))
  }, [open, slug])

  const dirty = items.join('\n') !== origJoined

  const handleAdd = () => {
    const t = draft.trim()
    if (!t || items.includes(t)) return
    setItems([...items, t])
    setDraft('')
  }
  const handleRemove = (i: number) => setItems(items.filter((_, idx) => idx !== i))

  const handleSave = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.setModelNegatives(slug, items)
      await onSaved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Edit negatives · {phrase}</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          Adversarial phrases the model must NOT fire on. Each entry gets
          TTS-synthesized as a negative training clip. Editing this list +
          retraining wipes the stale negative clips automatically so the
          changes take effect.
        </Typography>

        <Stack direction="row" sx={{ gap: 1, mb: 1.5 }}>
          <TextField
            size="small"
            fullWidth
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. hey siri"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && draft.trim() && !busy) handleAdd()
            }}
            disabled={busy}
          />
          <Button
            size="small"
            variant="outlined"
            startIcon={<AddIcon />}
            disabled={busy || !draft.trim()}
            onClick={handleAdd}
          >
            Add
          </Button>
        </Stack>

        <Box
          sx={{
            maxHeight: 360,
            overflowY: 'auto',
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            p: 0.5,
          }}
        >
          {items.length === 0 ? (
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ fontStyle: 'italic', p: 1, display: 'block' }}
            >
              No negatives yet. The shipped template defaults will be used at training time.
            </Typography>
          ) : (
            <Stack sx={{ gap: 0.25 }}>
              {items.map((p, i) => (
                <Box
                  key={`${p}-${i}`}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    px: 1,
                    py: 0.25,
                    borderRadius: 0.5,
                    '&:hover': { bgcolor: 'action.hover' },
                  }}
                >
                  <Typography
                    variant="body2"
                    sx={{
                      flex: 1,
                      fontFamily: 'ui-monospace, monospace',
                      fontSize: '0.8rem',
                    }}
                  >
                    {p}
                  </Typography>
                  <Tooltip title="Remove">
                    <span>
                      <IconButton size="small" disabled={busy} onClick={() => handleRemove(i)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Box>
              ))}
            </Stack>
          )}
        </Box>

        <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1 }}>
          {items.length} entries · {dirty ? 'unsaved changes' : 'saved'}
        </Typography>
        {error && (
          <Typography variant="caption" color="error.main" sx={{ display: 'block', mt: 0.5 }}>
            {error}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={handleSave} disabled={busy || !dirty}>
          {busy ? 'Saving…' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
