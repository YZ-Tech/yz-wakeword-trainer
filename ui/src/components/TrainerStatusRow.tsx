import { Stack } from '@mui/material'
import type { BackgroundItem, ModelView } from '../types'
import { useCapabilities } from '../lib/capabilities'
import { DatasetPanel } from './DatasetPanel'
import { DeployedPanel } from './DeployedPanel'
import { RoomPanel } from './RoomPanel'
import { WslOutputPanel } from './WslOutputPanel'

/** The status-panel row at the top of TrainerCard. In JarvYZ-embedded
 *  mode this is 4 panels (deployed / output / dataset / room). In
 *  standalone-via-satellite mode, "deployed" and "latest output" are
 *  meaningless (no JarvYZ live runtime to deploy to), so we hide them
 *  and show only dataset + room. Gated on capabilities.deployTarget. */
export function TrainerStatusRow({
  model,
  backgrounds,
  recording,
  onRoomClick,
}: {
  model: ModelView
  backgrounds: BackgroundItem[]
  recording: boolean
  onRoomClick: () => void
}) {
  const { deployTarget } = useCapabilities()
  const showJarvYZPanels = deployTarget === 'jarvis'
  return (
    <Stack
      direction={{ xs: 'column', md: 'row' }}
      sx={{ gap: 2, mb: 2 }}
    >
      {showJarvYZPanels && <DeployedPanel deployed={model.deployed} />}
      {showJarvYZPanels && (
        <WslOutputPanel dataset={model.dataset} deployed={model.deployed} />
      )}
      <DatasetPanel dataset={model.dataset} />
      <RoomPanel items={backgrounds} recording={recording} onClick={onRoomClick} />
    </Stack>
  )
}
