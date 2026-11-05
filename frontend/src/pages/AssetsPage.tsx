import { useNavigate, useSearchParams } from "react-router-dom";
import type { AssetFilters, AssetStatus, Kind } from "../api/client";
import { useAssets, useMeta, useProjects } from "../api/queries";
import { AssetStatus as AssetStatusView } from "../components/AssetStatus";
import { KIND_LABEL, STATUS_LABEL, label, timeAgo } from "../labels";
import {
  Box,
  EmptyState,
  ErrorNotice,
  Field,
  LinkButton,
  Loading,
  PageHeader,
  Row,
  Select,
  Stack,
  Table,
  TableRow,
  Text,
} from "../ui/kit";

const STATUSES: AssetStatus[] = ["queued", "running", "waiting", "done", "failed"];

export function AssetsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const filters: AssetFilters = {
    project_id: params.get("project") || undefined,
    kind: (params.get("kind") as Kind) || undefined,
    status: (params.get("status") as AssetStatus) || undefined,
  };
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
  const filtered = !!(filters.project_id || filters.kind || filters.status);

  return (
    <Stack gap={4}>
      <PageHeader
        title="Assets"
        subtitle="Everything generated so far. Status updates live."
        actions={
          <LinkButton to="/new" variant="primary">
            New asset
          </LinkButton>
        }
      />
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
