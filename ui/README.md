# Wakeword Trainer UI

The wake-word trainer's React UI, extracted as a standalone Vite project so
the same source ships as:

- An **IIFE module** consumed by JarvYZ's frontend via
  [@yz-dev/react-dynamic-module](https://github.com/YeonV/react-dynamic-module)
  (`build:lib` → `public/yz-wakeword.iife.js`)
- A **standalone SPA** that the satellite can serve directly (`build:pages` → `dist/`) — Phase 2

## Module contract

The IIFE attaches to `window.YzWakeword.WakeWordTab`. It accepts:

```ts
interface WakeWordTabProps {
  theme: Theme              // MUI Theme — host passes useTheme() value
  wsApi: WSApi              // host's WS API: {send, subscribe, isConnected}
  capabilities: {
    apiBase: string         // prefix for all API calls (e.g. '' in JarvYZ)
    deployTarget: 'jarvis' | 'download'
    // (more flags as standalone path is fleshed out)
  }
}
```

Host responsibilities (see [ledfx audio-visualiser pattern](https://github.com/YeonV/ledfx/blob/master/frontend/src/components/AudioVisualiser/AudioVisualiser.tsx)):

- Pass `theme={useTheme()}` so the module's own `ThemeProvider` (seeded with that value)
  produces correct theming inside the module. (React context can't cross the
  bundle boundary; this prop side-channel sidesteps the identity problem.)
- Pass `wsApi={useWebSocket()}` so the module's internal `useSubscription` reads
  from a single shared WS — no second connection to the backend.
- Pass `capabilities` to flip mode-specific behavior (Deploy vs Download, etc.).

## Dev

```bash
npm install
npm run dev                  # standalone SPA dev server on http://127.0.0.1:5182
npm run build:lib            # produce dist-lib/yz-wakeword.iife.js
npm run install-to-frontend  # copy dist-lib/ output → ../frontend/public/modules/
npm run ship                 # = build:lib + install-to-frontend (one shot)
npm run build:pages          # produce dist/ for satellite static serving (Phase 2)
```

Typical iteration on the IIFE: edit source → `npm run ship` → reload JarvYZ frontend.
`install-to-frontend.mjs` is node-based (no `cp` / shell), so the same scripts
work from WSL bash, Windows cmd, or Git Bash.

## Related

- Architectural background: [`../WAKEWORD_TRAINER_ISOLATION.md`](../WAKEWORD_TRAINER_ISOLATION.md)
- Verification spike for MUI-bundling + theme-prop: `/tmp/wakeword-spike/`
- Memory: `dynamic_module_mui_theme_pattern`
