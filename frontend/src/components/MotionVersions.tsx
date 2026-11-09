// The versions a skill animation was made in (one prompt, other seeds): each as a stick-figure sheet
// with the judge's score. Clicking one uses it (only the clip is made again, a few seconds).

import { api, type Motion } from "../api/client";
import { keys, useApiMutation } from "../api/queries";
import { Badge, ErrorNotice, Grid, MediaCard, Stack, Text } from "../ui/kit";

export function MotionVersions({ motion }: { motion: Motion }) {
  const pick = useApiMutation((n: number) => api.pickMotion(motion.id, n), [keys.motions]);
  const versions = motion.candidates ?? [];
  if (!versions.length) return null;
  const busy = motion.status === "queued" || motion.status === "running";
  return (
    <Stack gap={2}>
      <Text small muted>
        Versions (front on top, right side below; left limbs blue, right red). The judge picked the best score;
        click another to use it.
      </Text>
      <Grid cols={2} min={360}>
        {versions.map((v) => (
          <MediaCard key={v.candidate} image={v.url} aspect={2} selected={motion.chosen === v.candidate}
                     onClick={busy || motion.chosen === v.candidate ? undefined : () => pick.mutate(v.candidate)}
                     title={`Version ${v.candidate}${motion.chosen === v.candidate ? " (used)" : ""}`}
                     subtitle={v.problems.length ? v.problems.join("; ") : undefined}
                     badges={v.match === null ? <Badge>Not judged</Badge>
                       : <Badge tone={v.match >= 7 ? "success" : v.match >= 4 ? "warning" : "danger"}>{v.match}/10</Badge>} />
        ))}
      </Grid>
      {pick.error && <ErrorNotice error={pick.error} />}
    </Stack>
  );
}
