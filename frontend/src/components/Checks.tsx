// Quality checks at the gates: what passed, what to look at, what failed.

import type { Check } from "../api/client";
import { Badge, Card, Row, Stack, Text } from "../ui/kit";

export const CHECK_TONE = { pass: "success", warn: "warning", fail: "danger" } as const;
export const CHECK_WORD = { pass: "OK", warn: "Look", fail: "Failed" } as const;

export function worstCheck(checks: Check[] | undefined): Check["status"] | null {
  if (!checks?.length) return null;
  return checks.some((c) => c.status === "fail") ? "fail" : checks.some((c) => c.status === "warn") ? "warn" : "pass";
}

/** The first check that did not pass, as a short badge (for candidate thumbnails). */
export function CheckBadge({ checks }: { checks: Check[] | undefined }) {
  const problem = checks?.find((c) => c.status === "fail") ?? checks?.find((c) => c.status === "warn");
  if (!problem) return checks?.length ? <Badge tone="success">Checks OK</Badge> : null;
  return <Badge tone={CHECK_TONE[problem.status]}>{problem.detail}</Badge>;
}

export function ChecksCard({ checks, subtitle }: { checks: Check[] | undefined; subtitle?: string }) {
  if (!checks?.length) return null;
  const status = worstCheck(checks)!;
  return (
    <Card title="Checks" subtitle={subtitle} actions={<Badge tone={CHECK_TONE[status]}>{CHECK_WORD[status]}</Badge>}>
      <Stack gap={2}>
        {checks.map((c) => (
          <Row key={c.id} gap={2}>
            <Badge tone={CHECK_TONE[c.status]} dot>{c.label}</Badge>
            <Text small muted>{c.detail}</Text>
          </Row>
        ))}
      </Stack>
    </Card>
  );
}
