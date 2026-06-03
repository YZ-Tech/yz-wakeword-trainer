import { useState } from 'react'
import {
  Box,
  Button,
  Card,
  CardContent,
  CardHeader,
  Chip,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import CompareIcon from '@mui/icons-material/Compare'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import EditIcon from '@mui/icons-material/Edit'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import StopIcon from '@mui/icons-material/Stop'
import UploadIcon from '@mui/icons-material/Upload'
import DownloadIcon from '@mui/icons-material/Download'
import { useApi } from '../lib/api'
import { useCapabilities } from '../lib/capabilities'
import { useLogTail } from '../hooks/useLogTail'
import { useCallback } from 'react'
import type { ModelView, WakewordStatus } from '../types'
import { CloneModelDialog } from './CloneModelDialog'
import { CompareModelsDialog } from './CompareModelsDialog'
import { Dot } from './Dot'
import { EditNegativesDialog } from './EditNegativesDialog'
import { LogPanel } from './LogPanel'
import { RecordDialog } from './RecordDialog'
import { RoomManageDialog } from './RoomManageDialog'
import { TrainerInfoDialog } from './TrainerInfoDialog'
import { TrainerStatusRow } from './TrainerStatusRow'
import { TrainPreflightDialog, type TrainPreflight } from './TrainPreflightDialog'

interface Props {
  status: WakewordStatus
  model: ModelView | null
  refresh: () => Promise<void>
  setError: (msg: string | null) => void
  error: string | null
}

export function TrainerCard({ status, model, refresh, setError, error }: Props) {
  const api = useApi()
  const { deployTarget } = useCapabilities()
  const [busy, setBusy] = useState<string | null>(null)
  const [roomBusy, setRoomBusy] = useState<string | null>(null)
  const [infoOpen, setInfoOpen] = useState(false)
  const [roomOpen, setRoomOpen] = useState(false)
  const [recordDialogOpen, setRecordDialogOpen] = useState(false)
  const [editNegOpen, setEditNegOpen] = useState(false)
  const [cloneOpen, setCloneOpen] = useState(false)
  const [compareOpen, setCompareOpen] = useState(false)
  const [preflight, setPreflight] = useState<TrainPreflight | null>(null)

  const wslAvail = status.wsl_available
  const train = status.train
  const record = status.record
  // Training of THIS specific model
  const trainingThis = train.running && train.slug === model?.slug

  const fetchTrainLog = useCallback(() => api.getTrainLog({ tail: 120 }), [api])
  const trainLog = useLogTail(fetchTrainLog, trainingThis, model?.slug)

  const wrap = (key: string, fn: () => Promise<unknown>) => async () => {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  // Smart Start: preflight first. If state is `fresh` or `clean`, fire
  // directly — no friction on the happy path. Otherwise open the dialog
  // with contextual Continue / Clear-and-start-over buttons so the user
  // picks what to do with the on-disk leftovers.
  const handleStart = async () => {
    if (!model?.slug) return
    setBusy('start')
    setError(null)
    try {
      const pre = await api.getTrainPreflight(model.slug)
      if (pre.state === 'fresh' || pre.state === 'clean') {
        await api.startTrain(model.slug)
        await refresh()
        return
      }
      setPreflight(pre)
      setBusy(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy((b) => (b === 'start' ? null : b))
    }
  }
  const handlePreflightChoice = async (wipeStale: boolean) => {
    if (!model?.slug) return
    setBusy('start')
    setError(null)
    try {
      await api.startTrain(model.slug, { wipeStale })
      setPreflight(null)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }
  const handleStop = wrap('stop', () => api.stopTrain())
  // In JarvYZ-embedded: push the .onnx into JarvYZ's active-models dir.
  // In standalone: trigger a browser download of the .onnx instead.
  const handleDeploy = wrap('deploy', async () => {
    if (!model?.slug) return
    if (deployTarget === 'jarvis') await api.deployModel(model.slug)
    else await api.downloadOnnx(model.slug)
  })

  const handleRecordStart = async (minutes: number) => {
    setRoomBusy('start')
    setError(null)
    try {
      await api.startRoomRecord(minutes)
      setRecordDialogOpen(false)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRoomBusy(null)
    }
  }

  const handleRecordStop = async () => {
    setRoomBusy('stop')
    try {
      await api.stopRoomRecord()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRoomBusy(null)
    }
  }

  const handleRecordDelete = async (name: string) => {
    try {
      await api.deleteBackground(name)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (!model) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Typography variant="caption" color="text.disabled" sx={{ fontStyle: 'italic' }}>
            No wake word selected. Pick one from the list above, or click "New" to define a
            new one.
          </Typography>
        </CardContent>
      </Card>
    )
  }

  const hasOnnx = (model.dataset.wsl_onnx_size ?? 0) > 0

  return (
    <Card variant="outlined">
      <CardHeader
        title={
          <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
            <Dot on={wslAvail} />
            <Typography variant="subtitle1">Trainer</Typography>
            <Tooltip title="What this does + why WSL is involved">
              <IconButton size="small" onClick={() => setInfoOpen(true)} sx={{ p: 0.25 }}>
                <InfoOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            {trainingThis && (
              <Chip size="small" label="training" color="primary" sx={{ height: 20 }} />
            )}
            {train.running && !trainingThis && train.slug && (
              <Chip
                size="small"
                label={`training ${train.slug}`}
                sx={{ height: 20, fontSize: '0.65rem' }}
              />
            )}
          </Stack>
        }
        subheader={model.phrase}
        sx={{ pb: 0 }}
      />
      <CardContent>
        <TrainerStatusRow
          model={model}
          backgrounds={status.backgrounds.bg_list}
          recording={record.running}
          onRoomClick={() => setRoomOpen(true)}
        />

        {/* Primary lifecycle (left) + admin/edit (right, icon-only with
            tooltips). Visual hierarchy makes the Start/Stop button the
            obvious action; Edit/Clone/Compare are available but recede. */}
        <Stack direction="row" sx={{ gap: 1, alignItems: 'center', mb: 2 }}>
          {!trainingThis ? (
            <Button
              variant="contained"
              startIcon={<PlayArrowIcon />}
              onClick={handleStart}
              disabled={!wslAvail || busy !== null || train.running}
            >
              {busy === 'start' ? 'Starting…' : 'Start training'}
            </Button>
          ) : (
            <Button
              variant="contained"
              color="error"
              startIcon={<StopIcon />}
              onClick={handleStop}
              disabled={busy !== null}
            >
              {busy === 'stop' ? 'Stopping…' : 'Stop training'}
            </Button>
          )}
          <Button
            variant="outlined"
            size="small"
            startIcon={deployTarget === 'jarvis' ? <UploadIcon /> : <DownloadIcon />}
            onClick={handleDeploy}
            disabled={!wslAvail || busy !== null || !hasOnnx}
          >
            {busy === 'deploy'
              ? (deployTarget === 'jarvis' ? 'Deploying…' : 'Downloading…')
              : (deployTarget === 'jarvis' ? 'Deploy → Windows' : 'Download .onnx')}
          </Button>
          <Box sx={{ flex: 1 }} />
          <Tooltip title={`Edit negatives (${(model.negatives ?? []).length})`}>
            <span>
              <IconButton size="small" onClick={() => setEditNegOpen(true)} disabled={busy !== null}>
                <EditIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Clone this model into a new slug">
            <span>
              <IconButton size="small" onClick={() => setCloneOpen(true)} disabled={busy !== null}>
                <ContentCopyIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Compare with other models">
            <span>
              <IconButton size="small" onClick={() => setCompareOpen(true)} disabled={busy !== null}>
                <CompareIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>

        {trainingThis && (
          <Typography
            variant="caption"
            sx={{
              fontFamily: 'ui-monospace, monospace',
              color: 'text.disabled',
              display: 'block',
              mb: 0.5,
            }}
          >
            {train.cmd}
          </Typography>
        )}
        <LogPanel text={trainLog} height={trainingThis ? 260 : 140} testid="train-log-panel" />

        {error && (
          <Typography variant="caption" color="error.main" sx={{ display: 'block', mt: 1 }}>
            {error}
          </Typography>
        )}
      </CardContent>

      <TrainerInfoDialog open={infoOpen} onClose={() => setInfoOpen(false)} model={model} />
      <RoomManageDialog
        open={roomOpen}
        onClose={() => setRoomOpen(false)}
        items={status.backgrounds.bg_list}
        running={record.running}
        busy={roomBusy}
        onRecordClick={() => setRecordDialogOpen(true)}
        onStop={handleRecordStop}
        onDelete={handleRecordDelete}
      />
      <RecordDialog
        open={recordDialogOpen}
        onClose={() => setRecordDialogOpen(false)}
        onStart={handleRecordStart}
        busy={roomBusy}
      />
      <EditNegativesDialog
        open={editNegOpen}
        onClose={() => setEditNegOpen(false)}
        slug={model.slug}
        phrase={model.phrase}
        onSaved={refresh}
      />
      <CloneModelDialog
        open={cloneOpen}
        onClose={() => setCloneOpen(false)}
        source={model}
        onCreated={async () => {
          await refresh()
        }}
      />
      <CompareModelsDialog
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        initialSelection={[model.slug]}
        models={status.models}
        onAfterAction={refresh}
      />
      <TrainPreflightDialog
        open={preflight !== null}
        preflight={preflight}
        onCancel={() => setPreflight(null)}
        onContinue={() => handlePreflightChoice(false)}
        onClearAndStart={() => handlePreflightChoice(true)}
        busy={busy === 'start'}
      />
    </Card>
  )
}
