import { useEffect, useRef, useState } from 'react'
import {
  Box,
  Button,
  Card,
  CardContent,
  CardHeader,
  Chip,
  FormControl,
  IconButton,
  InputLabel,
  LinearProgress,
  ListSubheader,
  MenuItem,
  Select,
  Slider,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import MicIcon from '@mui/icons-material/Mic'
import MicOffIcon from '@mui/icons-material/MicOff'
import { useApi } from '../lib/api'
import type { AudioDevicesResponse, SpikeStatus } from '../lib/api'

const POLL_HZ = 5  // poll /spike/status at 5 Hz while running

/** Producer-side live model test. Runs the trained ONNX in the satellite
 *  against the satellite's mic; UI polls /spike/status for scores. Same
 *  code in JarvYZ-embedded mode (JarvYZ proxies to satellite) and
 *  standalone mode (UI hits satellite native paths).
 *
 *  Audio device selector mirrors the convention from Settings →
 *  AudioDeviceSelect.tsx (hostapi-grouped Select, indices as values).
 *  Threshold slider is UI-only — the satellite always emits the raw
 *  score; only the FIRED chip uses the threshold for display. */
export function BrowserSpikeCard({ slug }: { slug: string | null }) {
  const api = useApi()
  const [s, setS] = useState<SpikeStatus | null>(null)
  const [busy, setBusy] = useState<'start' | 'stop' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [devices, setDevices] = useState<AudioDevicesResponse | null>(null)
  // null = "system default" sentinel (satellite resolves the OS default)
  const [device, setDevice] = useState<number | null>(null)
  const [threshold, setThreshold] = useState(0.5)
  const pollRef = useRef<number | null>(null)

  // Initial fetches: current spike state + device list
  useEffect(() => {
    api.getSpikeStatus().then(setS).catch(() => {})
    api.listAudioInputs().then(setDevices).catch(() => {})
  }, [api])

  // Polling effect: active only while s.running. Stops on unmount.
  useEffect(() => {
    if (!s?.running) {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }
    if (pollRef.current !== null) return
    pollRef.current = window.setInterval(() => {
      api
        .getSpikeStatus()
        .then(setS)
        .catch(() => {})
    }, 1000 / POLL_HZ)
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [s?.running, api])

  const handleStart = async () => {
    if (!slug) return
    setBusy('start')
    setError(null)
    try {
      const next = await api.startSpike(slug, device)
      setS(next)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const handleStop = async () => {
    setBusy('stop')
    setError(null)
    try {
      const next = await api.stopSpike()
      setS(next)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const running = s?.running ?? false
  const latestScore = s?.latest_score ?? 0
  const peakScore = s?.peak_score ?? 0
  const isFiring = latestScore >= threshold
  const runningSlug = s?.slug ?? ''
  const slugMismatch = running && !!slug && !!runningSlug && runningSlug !== slug

  // Group devices by host_api so the Select renders MME / WASAPI / etc. as separators
  const grouped = (() => {
    if (!devices) return [] as Array<{ host: string; devs: AudioDevicesResponse['devices'] }>
    const map = new Map<number, AudioDevicesResponse['devices']>()
    for (const d of devices.devices) {
      const arr = map.get(d.host_api) ?? []
      arr.push(d)
      map.set(d.host_api, arr)
    }
    const apiName = (idx: number) =>
      devices.host_apis.find((h) => h.index === idx)?.name ?? `api${idx}`
    return [...map.entries()]
      .sort(([a], [b]) => a - b)
      .map(([hostIdx, devs]) => ({ host: apiName(hostIdx), devs }))
  })()

  return (
    <Card variant="outlined">
      <CardHeader
        title={
          <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
            <Typography variant="subtitle1">Test model</Typography>
            <Tooltip title="Producer-side test: the trained ONNX runs in the satellite against its mic. Score updates ~12.5×/sec; FIRED chip lights above the threshold.">
              <IconButton size="small" sx={{ p: 0.25 }}>
                <InfoOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            {running && (
              <Chip
                size="small"
                label={`testing ${runningSlug}`}
                color="primary"
                sx={{ height: 20, fontSize: '0.65rem' }}
              />
            )}
            {isFiring && (
              <Chip
                size="small"
                label="FIRED"
                color="success"
                sx={{ height: 20, fontWeight: 700 }}
              />
            )}
          </Stack>
        }
        subheader={
          slug
            ? `Speak '${slug.replace(/_/g, ' ')}' at the chosen mic`
            : 'Select a model in the list above'
        }
        action={
          !running ? (
            <Button
              size="small"
              variant="contained"
              startIcon={<MicIcon />}
              onClick={handleStart}
              disabled={busy !== null || !slug}
            >
              {busy === 'start' ? 'Starting…' : 'Start mic test'}
            </Button>
          ) : (
            <Button
              size="small"
              variant="outlined"
              color="error"
              startIcon={<MicOffIcon />}
              onClick={handleStop}
              disabled={busy !== null}
            >
              {busy === 'stop' ? 'Stopping…' : 'Stop'}
            </Button>
          )
        }
        sx={{ pb: 0 }}
      />
      <CardContent>
        <Stack sx={{ gap: 2 }}>
          {/* Device selector — mirrors the Settings AudioDeviceSelect
              pattern (hostapi-grouped, indices as values, "System default"
              sentinel as null). Disabled mid-run; user stops to switch. */}
          <FormControl size="small" disabled={running || !devices}>
            <InputLabel>Audio input</InputLabel>
            <Select
              value={device === null ? '__default__' : String(device)}
              label="Audio input"
              onChange={(e) => {
                const v = e.target.value
                if (v === '__default__') setDevice(null)
                else setDevice(parseInt(v, 10))
              }}
              renderValue={(sel) => {
                if (sel === '__default__') {
                  const def = devices?.default_input
                  const defDev = def !== null && def !== undefined
                    ? devices?.devices.find((d) => d.index === def)
                    : undefined
                  return (
                    <span>
                      <em>System default</em>
                      {defDev && (
                        <Typography component="span" variant="caption" sx={{ ml: 1, color: 'text.disabled' }}>
                          ({defDev.name})
                        </Typography>
                      )}
                    </span>
                  )
                }
                const idx = parseInt(sel, 10)
                const dev = devices?.devices.find((d) => d.index === idx)
                return dev ? `#${dev.index} ${dev.name}` : sel
              }}
            >
              <MenuItem value="__default__">
                <em>System default</em>
              </MenuItem>
              {grouped.flatMap(({ host, devs }) => [
                <ListSubheader key={`hdr-${host}`}>{host}</ListSubheader>,
                ...devs.map((d) => (
                  <MenuItem key={d.index} value={String(d.index)}>
                    <MicIcon fontSize="small" sx={{ mr: 1 }} />
                    #{d.index} {d.name}
                  </MenuItem>
                )),
              ])}
            </Select>
          </FormControl>

          {/* Current score readout + meter */}
          <Box>
            <Stack direction="row" sx={{ alignItems: 'baseline', gap: 1.5, mb: 0.5 }}>
              <Typography
                variant="h4"
                sx={{
                  fontFamily: 'ui-monospace, monospace',
                  fontVariantNumeric: 'tabular-nums',
                  color: isFiring ? 'success.main' : 'text.primary',
                  lineHeight: 1,
                }}
              >
                {latestScore.toFixed(3)}
              </Typography>
              <Typography variant="caption" color="text.disabled">
                · peak {peakScore.toFixed(3)}
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={Math.min(100, latestScore * 100)}
              sx={{
                height: 8,
                borderRadius: 1,
                '& .MuiLinearProgress-bar': {
                  bgcolor: isFiring ? 'success.main' : 'primary.main',
                  transition: 'transform 100ms linear',
                },
              }}
            />
          </Box>

          {/* Threshold slider — UI-only. Determines when the FIRED chip lights. */}
          <Box>
            <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mb: 0.5 }}>
              <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
                Fire threshold
              </Typography>
              <Typography
                variant="caption"
                sx={{ fontFamily: 'ui-monospace, monospace', fontVariantNumeric: 'tabular-nums' }}
              >
                {threshold.toFixed(2)}
              </Typography>
            </Stack>
            <Slider
              size="small"
              value={threshold}
              min={0}
              max={1}
              step={0.01}
              onChange={(_, v) => setThreshold(Array.isArray(v) ? v[0] : v)}
            />
          </Box>

          {slugMismatch && (
            <Typography variant="caption" color="warning.main">
              Test is running for <code>{runningSlug}</code>, not the model you have
              selected (<code>{slug}</code>). Stop + restart to switch.
            </Typography>
          )}

          {s?.error && (
            <Typography variant="caption" color="error.main">
              {s.error}
            </Typography>
          )}
          {error && (
            <Typography variant="caption" color="error.main">
              {error}
            </Typography>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}
