import { useEffect, useRef, useState } from 'react'
import { Badge, Box, CircularProgress, IconButton, Stack, Tooltip, Typography } from '@mui/material'
import { ThemeProvider, type Theme } from '@mui/material/styles'
import SettingsIcon from '@mui/icons-material/Settings'
import { ApiContext, type WakeWordApi } from './lib/api'
import { WSContext, type WSApi } from './lib/ws'
import { CapabilitiesContext, DEFAULT_CAPABILITIES, type Capabilities } from './lib/capabilities'
import { BrowserSpikeCard } from './components/BrowserSpikeCard'
import { CorporaSetupDialog } from './components/CorporaSetupDialog'
import { ModelsCard } from './components/ModelsCard'
import { TrainerCard } from './components/TrainerCard'
import { useWakewordSnapshot } from './hooks/useWakewordSnapshot'

export interface WakeWordTabProps {
  /** Host's MUI theme. Wrapped in our own ThemeProvider so module-side
   *  `useTheme()` reads it. See [[dynamic-module-mui-theme-pattern]]. */
  theme?: Theme
  /** Host's WS API. Injected into the module's WSContext so the module's
   *  `useSubscription` reads from the host's connection (no duplicate WS). */
  wsApi?: WSApi
  /** Host's WakeWord API implementation. The module never knows URLs —
   *  it calls named operations defined by the WakeWordApi interface. */
  api: WakeWordApi
  /** Mode flags: deployTarget = 'jarvis' (push to live runtime) or
   *  'download' (browser download). */
  capabilities?: Capabilities
}

/** Root export — JarvYZ (and the standalone SPA) load this via
 *  @yz-dev/react-dynamic-module. Wires up Theme/WS/Api/Capabilities
 *  contexts with host-injected values, then renders the inner tab. */
export function WakeWordTab({ theme, wsApi, api, capabilities }: WakeWordTabProps) {
  const caps = capabilities ?? DEFAULT_CAPABILITIES

  const inner = (
    <ApiContext.Provider value={api}>
      <WSContext.Provider value={wsApi ?? { send: () => {}, subscribe: () => () => {}, isConnected: false }}>
        <CapabilitiesContext.Provider value={caps}>
          <WakeWordTabInner />
        </CapabilitiesContext.Provider>
      </WSContext.Provider>
    </ApiContext.Provider>
  )

  return theme ? <ThemeProvider theme={theme}>{inner}</ThemeProvider> : inner
}

function WakeWordTabInner() {
  const { status, error, loaded, refresh, setError } = useWakewordSnapshot()
  const [selectedSlug, setSelectedSlug] = useState<string>('')
  const [corporaOpen, setCorporaOpen] = useState(false)
  const didInitialCorporaCheck = useRef(false)
  const corporaReady = status.corpora?.ready?.ready ?? null

  useEffect(() => {
    if (selectedSlug && status.models.some((m) => m.slug === selectedSlug)) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (
      status.train.running &&
      status.train.slug &&
      status.models.some((m) => m.slug === status.train.slug)
    ) {
      setSelectedSlug(status.train.slug)
    } else if (status.models.length > 0) {
      setSelectedSlug(status.models[0].slug)
    }
  }, [status.models, selectedSlug, status.train.running, status.train.slug])

  const selectedModel = status.models.find((m) => m.slug === selectedSlug) ?? null

  useEffect(() => {
    if (didInitialCorporaCheck.current) return
    if (corporaReady === null) return
    didInitialCorporaCheck.current = true
    if (!corporaReady) setCorporaOpen(true)
  }, [corporaReady])

  return (
    <>
      <Stack direction="row" sx={{ alignItems: 'center', mb: 1.5 }}>
        <Box sx={{ flex: 1 }} />
        <Tooltip
          title={
            corporaReady === false
              ? 'Training audio incomplete — open setup'
              : 'Open training-audio setup'
          }
        >
          <IconButton size="small" onClick={() => setCorporaOpen(true)} sx={{ ml: 1 }}>
            <Badge variant="dot" color="warning" invisible={corporaReady !== false}>
              <SettingsIcon fontSize="small" />
            </Badge>
          </IconButton>
        </Tooltip>
      </Stack>

      {!loaded ? (
        <Stack
          sx={{
            alignItems: 'center',
            justifyContent: 'center',
            gap: 1.5,
            py: 8,
            color: 'text.secondary',
          }}
        >
          {error ? (
            <Typography variant="body2" color="error.main">
              Wake-word trainer unreachable — {error}
            </Typography>
          ) : (
            <>
              <CircularProgress size={28} />
              <Typography variant="body2">Loading wake-word trainer…</Typography>
            </>
          )}
        </Stack>
      ) : (
      <Stack
        direction={{ xs: 'column', lg: 'row' }}
        sx={{ gap: 2, alignItems: 'flex-start' }}
      >
        <Stack sx={{ flex: 1, minWidth: 0, gap: 2 }}>
          <ModelsCard
            models={status.models}
            selectedSlug={selectedSlug}
            onSelect={setSelectedSlug}
            trainingSlug={status.train.slug}
            onError={setError}
            onAfterAction={refresh}
          />
          <BrowserSpikeCard slug={selectedModel?.slug ?? null} />
        </Stack>
        <Stack sx={{ flex: 2, minWidth: 0 }}>
          <TrainerCard
            status={status}
            model={selectedModel}
            refresh={refresh}
            setError={setError}
            error={error}
          />
        </Stack>
      </Stack>
      )}

      <CorporaSetupDialog
        open={corporaOpen}
        onClose={() => setCorporaOpen(false)}
        status={status}
        onError={setError}
        onAfterAction={refresh}
        error={error}
      />
    </>
  )
}
