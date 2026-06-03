import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useApi } from '../lib/api'
import type { ModelView } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  source: ModelView | null
  onCreated: (slug: string) => void
}

/** Shallow-clone a model. Carries phrase + language + negatives into a
 *  new slug; the variant trains from scratch on first /train.
 *  Used to A/B negatives changes without destroying the parent's data. */
export function CloneModelDialog({ open, onClose, source, onCreated }: Props) {
  const [newSlug, setNewSlug] = useState('')
  const api = useApi()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Default to `<src>_v2`, bump if it'd collide visually (user can edit anyway)
  const defaultSlug = useMemo(() => (source ? `${source.slug}_v2` : ''), [source])

  useEffect(() => {
    if (open) {
      setNewSlug(defaultSlug)
      setError(null)
    }
  }, [open, defaultSlug])

  const valid = /^[a-z0-9_]+$/.test(newSlug.trim())

  const handleClone = async () => {
    if (!source || !valid) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.cloneModel(source.slug, newSlug.trim())
      onCreated(r.slug)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!source) return null

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Clone "{source.phrase}"</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Creates a fresh variant carrying the same phrase, language, and
          negative-phrase list. Training data is NOT copied — the variant
          generates its own on first "Start training". Useful for A/B-ing
          negatives changes without touching the parent's data.
        </Typography>

        <Stack sx={{ gap: 2 }}>
          <TextField
            label="New slug"
            value={newSlug}
            onChange={(e) => setNewSlug(e.target.value)}
            fullWidth
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter' && valid && !busy) handleClone()
            }}
            helperText={
              valid
                ? `meta: ~/.jarvyz/wakeword/models/${newSlug}.json · trains on first Start training`
                : 'must match [a-z0-9_]+'
            }
            error={newSlug.length > 0 && !valid}
          />

          <Box sx={{ borderRadius: 1, border: 1, borderColor: 'divider', p: 1.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
              Inheriting from {source.slug}
            </Typography>
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ display: 'block', mt: 0.5 }}
            >
              · phrase: "{source.phrase}"
              <br />
              · language: {source.language}
              <br />
              · negatives: {(source.negatives ?? []).length} entries
            </Typography>
          </Box>

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
        <Button variant="contained" onClick={handleClone} disabled={!valid || busy}>
          {busy ? 'Cloning…' : 'Clone'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
