// Lib (IIFE) entry. The IIFE attaches these exports to `window.YzWakeword`;
// JarvYZ loads it via @yz-dev/react-dynamic-module.
export { WakeWordTab } from './WakeWordTab'
export type { WakeWordTabProps } from './WakeWordTab'
export type { WSApi } from './lib/ws'
export type { Capabilities } from './lib/capabilities'
export { createSatelliteApi } from './lib/api'
export type {
  WakeWordApi,
  CreateModelInput,
  StartTrainOptions,
  GetLogOptions,
  SettingsSnapshot,
  CorporaSnapshot,
} from './lib/api'
