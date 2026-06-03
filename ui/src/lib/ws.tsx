// Vendored WS context. Same shape as frontend/src/websocket/context.ts.
//
// Module strategy: own a WSContext at module-init time, expose useSubscription
// that reads from it. The module's root component does:
//
//   <WSContext.Provider value={props.wsApi}>...</WSContext.Provider>
//
// — injecting the HOST's WS API into the module's own context. Same idea
// as the theme prop pattern: React context can't cross the bundle
// boundary by identity, but the *value* travels fine through props. One
// WS connection, two providers (host's, module's), same value piped in.
//
// In Pages/standalone mode, App.tsx owns its own minimal WSProvider that
// connects to the satellite (or to the same origin as the page) — see
// App.tsx for that wiring.

import { createContext, useContext, useEffect, useRef } from 'react'

export interface WSApi {
  send: (data: unknown) => void
  subscribe: (eventType: string, cb: (data: any) => void) => () => void
  isConnected: boolean
}

const NO_WS: WSApi = {
  send: () => {},
  subscribe: () => () => {},
  isConnected: false,
}

export const WSContext = createContext<WSApi>(NO_WS)

export const useWebSocket = () => useContext(WSContext)

/** Subscribe a component to a single WS event type. Callback is
 *  ref-stabilized — re-renders don't re-subscribe. Auto-cleans on unmount. */
export function useSubscription<T = any>(eventType: string, callback: (data: T) => void) {
  const { subscribe } = useWebSocket()
  const cbRef = useRef(callback)

  useEffect(() => {
    cbRef.current = callback
  })

  useEffect(() => {
    const handler = (data: T) => cbRef.current(data)
    return subscribe(eventType, handler)
  }, [eventType, subscribe])
}
