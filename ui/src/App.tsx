// Standalone SPA entry. Used by `vite dev` and `vite build --mode pages`.
// In this mode the page IS the trainer — no JarvYZ wrapper. We set up our
// own theme + capabilities and mount WakeWordTab.
//
// No WS bridge: the satellite has no `/ws` event broadcaster today. The
// module's `useSubscription` falls back to its built-in NO_WS sentinel
// (subscribe = no-op) when no `wsApi` prop is passed. State refreshes
// happen on action handlers (action-driven, not event-driven). If you
// ever need live multi-tab sync, the path is to add `WS /events` to the
// satellite (~80 LOC) and pass a wsApi prop here — no module code change.

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import { WakeWordTab } from './WakeWordTab'
import { createSatelliteApi } from './lib/api'
import type { Capabilities } from './lib/capabilities'

const api = createSatelliteApi({ apiBase: '' })

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#ff4d6d' },
    background: { default: '#0d0d12', paper: '#15151c' },
  },
})

const capabilities: Capabilities = {
  apiBase: '',
  deployTarget: 'download',
}

/** Standalone-only header strip — logo + satellite name. Not rendered
 *  when embedded in JarvYZ (JarvYZ owns its own nav chrome). */
function StandaloneHeader() {
  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        marginBottom: 16,
        paddingBottom: 12,
        borderBottom: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      <img src="/logo.svg" alt="" width={32} height={32} style={{ display: 'block' }} />
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <strong style={{ fontSize: '1.05rem', letterSpacing: '0.02em' }}>
          Wakeword Trainer
        </strong>
        <span style={{ fontSize: '0.75rem', opacity: 0.55 }}>satellite · standalone</span>
      </div>
    </header>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <div style={{ padding: 16, maxWidth: 1400, margin: '0 auto' }}>
        <StandaloneHeader />
        <WakeWordTab theme={theme} api={api} capabilities={capabilities} />
      </div>
    </ThemeProvider>
  </StrictMode>,
)
