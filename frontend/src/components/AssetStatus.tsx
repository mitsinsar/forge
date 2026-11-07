// The status of an asset, built from kit components: a Badge, plus a ProgressBar while it runs.

import type { Asset } from "../api/client";
import { STAGE_LABEL } from "../labels";
import { Badge, Box, ProgressBar, Stack, Text } from "../ui/kit";

type StatusFields = Pick<Asset, "status" | "stage" | "gate" | "step" | "progress" | "error">;

export function StatusBadge({ asset }: { asset: StatusFields }) {
  switch (asset.status) {
    case "queued":
      return <Badge dot>Queued · {STAGE_LABEL[asset.stage]}</Badge>;
    case "running":
      return (
        <Badge tone="info" dot pulse>
          Running · {STAGE_LABEL[asset.stage]}
        </Badge>
      );
    case "waiting":
      return (
        <Badge tone="accent" dot>
          Waiting at gate {asset.gate}
        </Badge>
      );
    case "done":
      return <Badge tone="success">Done</Badge>;
    case "failed":
      return <Badge tone="danger">Failed</Badge>;
    case "stopped":
      return <Badge>Stopped · {STAGE_LABEL[asset.stage]}</Badge>;
  }
}

/** Badge plus, while running, the current step and a progress bar. */
export function AssetStatus({ asset }: { asset: StatusFields }) {
  return (
    <Box minWidth={200}>
    <Stack gap={1}>
      <span>
        <StatusBadge asset={asset} />
      </span>
      {asset.status === "running" && (
        <>
          <Text small muted>
            {asset.step} · {Math.round(asset.progress * 100)}%
          </Text>
          <ProgressBar value={asset.progress} label={asset.step} />
        </>
      )}
      {asset.status === "failed" && asset.error && (
        <Text small faint>
          {asset.error}
        </Text>
      )}
    </Stack>
    </Box>
  );
}
