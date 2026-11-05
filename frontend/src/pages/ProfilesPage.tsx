import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Engine, type Profile } from "../api/client";
import { keys, useApiMutation, useProfiles, useProjects } from "../api/queries";
import { label } from "../labels";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  LinkButton,
  Loading,
  PageHeader,
  Row,
  Stack,
  Table,
  TableRow,
  Tabs,
  Text,
} from "../ui/kit";

type Group = Engine | "none";

export function ProfilesPage() {
  const navigate = useNavigate();
  const profiles = useProfiles();
  const projects = useProjects();
  const [group, setGroup] = useState<Group>("unity");
  const [deleting, setDeleting] = useState<Profile | null>(null);
  const remove = useApiMutation((id: string) => api.deleteProfile(id), [keys.profiles]);

  const project = (id: string | null) => projects.data?.find((p) => p.id === id);
  const groupOf = (p: Profile): Group => project(p.project_id)?.engine ?? "none";
  const all = profiles.data ?? [];
  const shown = all.filter((p) => groupOf(p) === group);
  const isDefault = (p: Profile) => projects.data?.some((x) => x.default_profile_id === p.id);
  const firstProject = projects.data?.find((p) => p.engine === group);

  return (
    <Stack gap={4}>
      <PageHeader
        title="Style profiles"
        subtitle="How a game's assets look: base model, LoRAs, references, camera, render settings and budgets."
        actions={
          <LinkButton variant="primary" to={`/profiles/new${firstProject ? `?project=${firstProject.id}` : ""}`}>
            New profile
          </LinkButton>
        }
      />
      <Tabs
        label="Engine"
        value={group}
        onChange={setGroup}
        items={[
          { value: "unity", label: "Unity", count: all.filter((p) => groupOf(p) === "unity").length },
          { value: "godot", label: "Godot", count: all.filter((p) => groupOf(p) === "godot").length },
          { value: "none", label: "No project", count: all.filter((p) => groupOf(p) === "none").length },
        ]}
      />
      <ErrorNotice error={profiles.error} />
      {profiles.isPending ? (
        <Loading />
      ) : !shown.length ? (
        <EmptyState
          title={group === "none" ? "No unassigned profiles" : `No ${label(group)} profiles yet`}
          body={group === "none" ? undefined : `Adding a ${label(group)} project makes a Default profile for it.`}
        />
      ) : (
        <Table head={["Name", "Project", "Base model", "LoRAs", "References", "Camera", ""]}>
          {shown.map((p) => (
            <TableRow key={p.id} onClick={() => navigate(`/profiles/${p.id}`)}>
              <td>
                <Row gap={2}>
                  <Text strong>{p.name}</Text>
                  {isDefault(p) && <Badge tone="accent">Default</Badge>}
                </Row>
              </td>
              <td>{project(p.project_id)?.name ?? <Text faint>—</Text>}</td>
              <td><Badge tone="info">{label(p.base_model)}</Badge></td>
              <td>{p.loras.length}</td>
              <td>{p.reference_images.length}</td>
              <td>{label(p.camera_preset)}</td>
              <td>
                <Row justify="end">
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={(e) => {
                      e.stopPropagation();
                      remove.reset();
                      setDeleting(p);
                    }}
                  >
                    Delete
                  </Button>
                </Row>
              </td>
            </TableRow>
          ))}
        </Table>
      )}
      <ConfirmDialog
        open={!!deleting}
        title={`Delete ${deleting?.name}?`}
        body="A project's default profile can't be deleted; pick another default for the project first."
        confirmLabel="Delete profile"
        busy={remove.isPending}
        error={remove.error}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })}
      />
    </Stack>
  );
}
