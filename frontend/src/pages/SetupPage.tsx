// Setup: the local services (restart, stop) and, for each part Forge needs, whether it is ready and the
// steps to install it. Served by GET /api/setup and /api/tools.

import { useQuery } from "@tanstack/react-query";
import { api, type SetupPart } from "../api/client";
import { keys, useApiMutation, useTools } from "../api/queries";
import {
  Badge,
  Button,
  Card,
  CodeBlock,
  ErrorNotice,
  Loading,
  PageHeader,
  Row,
  SectionTitle,
  Stack,
  Table,
  TableRow,
  Text,
} from "../ui/kit";

function Services() {
  const tools = useTools();
  const restart = useApiMutation((tool: string) => api.restartTool(tool), [keys.tools, ["setup"]]);
  const stop = useApiMutation((tool: string) => api.stopTool(tool), [keys.tools, ["setup"]]);
  const busy = (tool: string) =>
    (restart.isPending && restart.variables === tool) || (stop.isPending && stop.variables === tool);
  return (
    <Card
      title="Services"
      subtitle="The tools Forge talks to. Restart one that hangs or holds too much GPU memory; stop one to free the GPU for something else."
    >
      <Stack gap={3}>
        <Table head={["Service", "Status", "", ""]}>
          {(tools.data ?? []).map((t) => (
            <TableRow key={t.tool}>
              <td>
                <Text strong>{t.name}</Text>
              </td>
              <td>
                <Stack gap={1}>
                  <Badge tone={t.ok ? "success" : "danger"} dot pulse={busy(t.tool)}>
                    {restart.isPending && restart.variables === t.tool
                      ? "Restarting…"
                      : stop.isPending && stop.variables === t.tool
                        ? "Stopping…"
                        : t.tool === "rig" || t.tool === "blender"
                          ? t.ok ? "Installed" : "Missing"
                          : t.ok
                            ? "Running"
                            : "Not answering"}
                  </Badge>
                  <Text small faint>{t.detail}</Text>
                </Stack>
              </td>
              <td>
                {t.restartable ? (
                  <Button size="sm" busy={restart.isPending && restart.variables === t.tool}
                          disabled={restart.isPending || stop.isPending} onClick={() => restart.mutate(t.tool)}>
                    {t.ok ? "Restart" : "Start"}
                  </Button>
                ) : t.tool === "rig" || t.tool === "blender" ? (
                  <Text small faint>Runs per job</Text>
                ) : (
                  <Text small faint>No restart command</Text>
                )}
              </td>
              <td>
                {t.stoppable && (
                  <Button size="sm" variant="danger" busy={stop.isPending && stop.variables === t.tool}
                          disabled={!t.ok || restart.isPending || stop.isPending} onClick={() => stop.mutate(t.tool)}>
                    Stop
                  </Button>
                )}
              </td>
            </TableRow>
          ))}
        </Table>
        <Text small muted>
          Restart and Stop need a [restart.&lt;service&gt;] entry in backend/assetgen.toml; each part below shows its
          own as the last step. UniRig and Blender aren't services: Forge starts them for each job.
        </Text>
        <ErrorNotice error={restart.error ?? stop.error} />
      </Stack>
    </Card>
  );
}

function Part({ part, index }: { part: SetupPart; index: number }) {
  return (
    <Card
      title={`${index}. ${part.name}`}
      subtitle={part.role}
      actions={
        <Badge tone={part.ok ? "success" : part.required ? "danger" : "neutral"} dot>
          {part.ok ? "Ready" : part.required ? "Missing" : "Optional, not set up"}
        </Badge>
      }
    >
      <Stack gap={3}>
        <Stack gap={1}>
          {part.checks.map((c) => (
            <Row key={c.label} gap={2}>
              <Badge tone={c.ok ? "success" : c.optional ? "neutral" : "danger"} dot>
                {c.label}
              </Badge>
              <Text small muted>{c.detail}</Text>
            </Row>
          ))}
        </Stack>
        <details open={!part.ok && part.required}>
          <summary>
            <Text strong>{part.ok ? "How it was installed" : "Install it, step by step"}</Text>
          </summary>
          <ol className="setup-steps">
            {part.steps.map((step, i) => (
              <li key={i}>
                <Stack gap={2}>
                  <Text>{step.text}</Text>
                  {step.command && <CodeBlock code={step.command} />}
                </Stack>
              </li>
            ))}
          </ol>
        </details>
        <Text small faint>
          Reads: {Object.entries(part.settings).map(([k, v]) => `${k} = ${v || "(empty)"}`).join(" · ")}
        </Text>
      </Stack>
    </Card>
  );
}

export function SetupPage() {
  const setup = useQuery({ queryKey: ["setup"], queryFn: api.setup });
  const missing = (setup.data ?? []).filter((p) => p.required && !p.ok);
  return (
    <Stack gap={4}>
      <PageHeader
        title="Setup"
        subtitle="What Forge needs on this machine. Each part is checked live; anything missing has its install steps."
        actions={
          <Button busy={setup.isFetching} onClick={() => setup.refetch()}>
            Check again
          </Button>
        }
      />
      <Services />
      {setup.isPending ? (
        <Loading />
      ) : (
        <>
          <SectionTitle>
            {missing.length
              ? `${missing.length} required part${missing.length === 1 ? "" : "s"} missing: ${missing.map((p) => p.name).join(", ")}`
              : "Everything Forge needs is ready"}
          </SectionTitle>
          <Text small muted>
            Settings go in backend/assetgen.toml (TOML, one key per line), or environment variables named
            ASSETGEN_&lt;KEY&gt;. Restart Forge after changing them. Paths below are examples: use your own.
          </Text>
          {(setup.data ?? []).map((part, i) => (
            <Part key={part.id} part={part} index={i + 1} />
          ))}
        </>
      )}
      <ErrorNotice error={setup.error} />
    </Stack>
  );
}
