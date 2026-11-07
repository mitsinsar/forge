// Example sheets for LoRAs and style profiles: the same three subjects rendered with (and for
// LoRAs without) the style, so styles can be compared at a glance.

import { exampleUrl, type Examples, type Job } from "../api/client";
import { label, timeAgo } from "../labels";
import { Badge, Grid, MediaCard, ProgressBar, Stack, Text } from "../ui/kit";

export function JobProgress({ job }: { job: Job | null | undefined }) {
  if (!job || job.status === "done") return null;
  if (job.status === "failed") return <Text small muted>Last attempt failed: {job.error}</Text>;
  return <ProgressBar value={job.progress} label={job.status === "queued" ? "Waiting for the worker" : job.step} />;
}

export function ExampleSheet({ examples, compare }: { examples?: Examples; compare?: boolean }) {
  if (!examples?.items.length) return <Text small muted>No examples yet.</Text>;
  return (
    <Stack gap={3}>
      <Text small faint>
        Made {timeAgo(examples.made_at)}
        {examples.weight !== undefined ? ` at weight ${examples.weight}` : ""}
        {examples.base_model ? ` on ${label(examples.base_model)}` : ""}. Same subjects and seed every time.
      </Text>
      <Grid min={compare ? 260 : 160}>
        {examples.items.map((item) =>
          compare ? (
            <Grid key={item.name} cols={2}>
              <MediaCard image={exampleUrl(item.with)} title={label(item.name)} badges={<Badge tone="accent">With</Badge>} />
              {item.without && (
                <MediaCard image={exampleUrl(item.without)} title={label(item.name)} badges={<Badge>Without</Badge>} />
              )}
            </Grid>
          ) : (
            <MediaCard key={item.name} image={exampleUrl(item.with)} title={label(item.name)} subtitle={item.prompt} />
          ),
        )}
      </Grid>
    </Stack>
  );
}
