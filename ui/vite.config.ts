import { defineConfig, type UserConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// Mode 'lib': IIFE module loaded by host via @yz-dev/react-dynamic-module.
//   - Externalises react/react-dom (host injects via window globals).
//   - Bundles MUI/emotion (theme propagates via ledfx pattern: theme prop +
//     module's own ThemeProvider seeded with it.)
//
// Naming: matches the other satellites' convention. id=`wakeword-trainer`
// → window global `YzWakewordTrainer`, IIFE file `yz-wakeword-trainer.iife.js`.
// See SATELLITE_DYNAMIC_MODULES.md.
//
// Mode 'pages' (default): standalone SPA for satellite serving + dev.
const libConfig: UserConfig = {
  plugins: [react()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: 'dist-lib',
    emptyOutDir: true,
    lib: {
      entry: fileURLToPath(new URL('./src/index.ts', import.meta.url)),
      name: 'YzWakewordTrainer',
      formats: ['iife'],
      fileName: () => 'yz-wakeword-trainer.iife.js',
    },
    // CJS require shim — same gotcha music/people hit. Bundled CJS deps can do
    // a literal `require("react")` at runtime (e.g. zustand v5 via
    // `use-sync-external-store`). No such dep here today, but every satellite
    // IIFE carries the banner so adding one later can't silently break.
    rollupOptions: {
      external: ['react', 'react-dom'],
      output: {
        globals: { react: 'React', 'react-dom': 'ReactDOM' },
        exports: 'named',
        extend: true,
        banner:
          'var require = function(id) {' +
          ' if (id === "react") return window.React;' +
          ' if (id === "react-dom") return window.ReactDOM;' +
          ' throw new Error("require not handled: " + id);' +
          ' };',
      },
    },
  },
}

const SAT = process.env.VITE_SATELLITE_URL || 'http://127.0.0.1:9001'

const pagesConfig: UserConfig = {
  plugins: [react()],
  server: {
    port: 5182,
    host: '127.0.0.1',
    // Forward satellite-native paths to a running satellite. In production
    // the satellite serves the SPA itself (same origin → no proxy needed).
    proxy: {
      '/health': SAT,
      '/status': SAT,
      '/train': SAT,
      '/jobs': SAT,
      '/models': SAT,
      '/corpora': SAT,
      '/settings': SAT,
      '/negatives': SAT,
      '/backgrounds': SAT,
      '/record': SAT,
      '/spike': SAT,
      '/audio': SAT,
    },
  },
  build: {
    // Pages-mode output lands INSIDE the Python package so a `pip install`
    // user gets a working UI out of the box. The satellite's server.py
    // conditionally mounts /static when this dir is populated. To rebuild
    // the SPA into this location: `npm run build:pages`.
    outDir: fileURLToPath(new URL('../yz_wakeword_trainer/static', import.meta.url)),
    emptyOutDir: true,
  },
}

export default defineConfig(({ mode }) => (mode === 'lib' ? libConfig : pagesConfig))
