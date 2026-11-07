import { useNavigate, useSearchParams } from "react-router-dom";
import { api, assetFileUrl, localUrl, type Asset, type AssetFilters, type AssetStatus, type Kind } from "../api/client";
import { keys, useApiMutation, useAssets, useMeta, useProjects } from "../api/queries";
import { AssetStatus as AssetStatusView } from "../components/AssetStatus";
import { CHECK_TONE, worstCheck } from "../components/Checks";
import { KIND_LABEL, STATUS_LABEL, label, timeAgo } from "../labels";
import {
  Badge,
  Box,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Grid,
  LinkButton,
  Loading,
  MediaCard,
  PageHeader,
  Row,
  Select,
  Stack,
  Table,
  TableRow,
  Tabs,
  Text,
} from "../ui/kit";

const STATUSES: AssetStatus[] = ["queued", "running", "waiting", "done", "failed", "stopped"];

/** The gallery picture: the picked preview (or the first candidate); Godot assets play their walk sprites
 *  on hover. */
function cardMedia(asset: Asset): { image: string | null; frames?: string[]; fps?: number } {
  const image = localUrl(asset.urls?.thumb ?? null) ?? null;
  const sprites = asset.outputs.rig?.sprites;
  if (!sprites) return { image };
  const direction = sprites.directions[0];
  const byClip = sprites.files?.[direction] ?? {};
  const clip = byClip.walk ? "walk" : byClip.idle ? "idle" : Object.keys(byClip)[0];
  const frames = (clip ? byClip[clip] : []).map((f) => assetFileUrl(asset.id, f));
  return { image: image ?? frames[0] ?? null, frames, fps: sprites.fps };
}

/** Waiting assets: whether the current gate's checks found something. */
function GateCheckBadge({ asset }: { asset: Asset }) {
  const checks = asset.gate === 2 ? asset.outputs.mesh?.checks : asset.gate === 3 ? asset.outputs.rig?.checks : undefined;
  const status = worstCheck(checks);
  if (!status || status === "pass") return null;
  return <Badge tone={CHECK_TONE[status]}>{status === "fail" ? "Check failed" : "Check: look"}</Badge>;
}

const STATUS_TONE: Partial<Record<AssetStatus, "warning" | "danger" | "success" | "accent">> = {
  waiting: "warning",
  failed: "danger",
  done: "success",
  running: "accent",
};

export function AssetsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const filters: AssetFilters = {
    project_id: params.get("project") || undefined,
    kind: (params.get("kind") as Kind) || undefined,
    status: (params.get("status") as AssetStatus) || undefined,
    batch_id: params.get("batch") || undefined,
  };
  const view = params.get("view") === "table" ? "table" : "gallery";
  const meta = useMeta();
  const projects = useProjects();
  const assets = useAssets(filters);
  const projectName = (id: string) => projects.data?.find((p) => p.id === id)?.name ?? "—";

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const filtered = !!(filters.project_id || filters.kind || filters.status || filters.batch_id);
  const allAssets = useAssets({});
  const queued = (allAssets.data ?? []).filter((a) => a.status === "queued").length;
  const stopAll = useApiMutation(() => api.stopQueue(), [keys.assets]);

  return (
    <Stack gap={4}>
      <PageHeader
        title="Assets"
        subtitle="Everything generated so far. Status updates live."
        actions={
          <Row gap={2}>
            {queued > 0 && (
              <Button variant="danger" busy={stopAll.isPending} onClick={() => stopAll.mutate(undefined)}
                      title="Take every queued stage off the queue (each asset can be resumed); running ones finish their step">
                Stop all queued ({queued})
              </Button>
            )}
            <LinkButton to="/new" variant="primary">
              New asset
            </LinkButton>
          </Row>
        }
      />
      <Row gap={3}>
        <Tabs
          label="View"
          value={view}
          onChange={(v) => setFilter("view", v === "gallery" ? "" : v)}
          items={[
            { value: "gallery", label: "Gallery" },
            { value: "table", label: "Table" },
          ]}
        />
        <Tabs
          label="Show"
          value={filters.status === "waiting" ? "mine" : "all"}
          onChange={(v) => setFilter("status", v === "mine" ? "waiting" : "")}
          items={[
            { value: "all", label: "All" },
            { value: "mine", label: "Needs me" },
          ]}
        />
        {filters.batch_id && (
          <Button size="sm" variant="ghost" onClick={() => setFilter("batch", "")}>
            Showing one batch · show all
          </Button>
        )}
      </Row>
      <Row gap={3}>
        <Box minWidth={200}>
          <Field label="Project">
            {(id) => (
              <Select
                id={id}
                value={filters.project_id ?? ""}
                onChange={(e) => setFilter("project", e.target.value)}
                placeholder="All projects"
                options={(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
              />
            )}
          </Field>
        </Box>
        <Box minWidth={160}>
          <Field label="Kind">
            {(id) => (
              <Select
                id={id}
                value={filters.kind ?? ""}
                onChange={(e) => setFilter("kind", e.target.value)}
                placeholder="All kinds"
                options={(meta.data?.kinds ?? []).map((k) => ({ value: k, label: KIND_LABEL[k] }))}
              />
            )}
          </Field>
        </Box>
        <Box minWidth={160}>
          <Field label="Status">
            {(id) => (
              <Select
                id={id}
                value={filters.status ?? ""}
                onChange={(e) => setFilter("status", e.target.value)}
                placeholder="Any status"
                options={STATUSES.map((s) => ({ value: s, label: STATUS_LABEL[s] }))}
              />
            )}
          </Field>
        </Box>
      </Row>

      <ErrorNotice error={assets.error} />
      {assets.isPending ? (
        <Loading />
      ) : !assets.data?.length ? (
        filtered ? (
          <EmptyState title="No assets match these filters" />
        ) : projects.data?.length === 0 ? (
          <EmptyState
            title="Register a game project first"
            body="Assets are generated for a Unity or Godot project."
            action={<LinkButton to="/projects" variant="primary">Add a project</LinkButton>}
          />
        ) : (
          <EmptyState
            title="No assets yet"
            body="Describe a hero, monster, boss, gear or weapon and the generator takes it from there."
            action={<LinkButton to="/new" variant="primary">New asset</LinkButton>}
          />
        )
      ) : view === "gallery" ? (
        <Grid min={190}>
          {assets.data.map((asset) => (
            <MediaCard
              key={asset.id}
              {...cardMedia(asset)}
              title={asset.name}
              subtitle={`${KIND_LABEL[asset.kind]} · ${label(asset.slot ?? asset.weapon_class ?? asset.archetype)} · ${projectName(asset.project_id)}`}
              badges={
                <>
                  <Badge tone={STATUS_TONE[asset.status]} dot={asset.status === "running"} pulse={asset.status === "running"}>
                    {asset.status === "waiting" && asset.gate ? `Gate ${asset.gate}` : STATUS_LABEL[asset.status]}
                  </Badge>
                  {asset.status === "waiting" && <GateCheckBadge asset={asset} />}
                  {asset.autopilot && asset.status !== "done" && <Badge tone="info">Autopilot</Badge>}
                </>
              }
              onClick={() => navigate(`/asset/${asset.id}`)}
            />
          ))}
        </Grid>
      ) : (
        <Table head={["Name", "Kind", "Project", "Status", "Updated"]}>
          {assets.data.map((asset) => (
            <TableRow key={asset.id} onClick={() => navigate(`/asset/${asset.id}`)}>
              <td>
                <Stack gap={1}>
                  <Text strong>{asset.name}</Text>
                  <Text small faint>
                    {asset.description.length > 80 ? `${asset.description.slice(0, 80)}…` : asset.description}
                  </Text>
                </Stack>
              </td>
              <td>
                <Stack gap={1}>
                  <span>{KIND_LABEL[asset.kind]}</span>
                  <Text small faint>
                    {label(asset.slot ?? asset.weapon_class ?? asset.archetype)}
                  </Text>
                </Stack>
              </td>
              <td>{projectName(asset.project_id)}</td>
              <td>
                <AssetStatusView asset={asset} />
              </td>
              <td>
                <Text muted small>
                  {timeAgo(asset.updated_at)}
                </Text>
              </td>
            </TableRow>
          ))}
        </Table>
      )}
    </Stack>
  );
}
