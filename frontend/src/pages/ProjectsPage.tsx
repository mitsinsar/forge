import { useState } from "react";
import { api, type Engine, type Project } from "../api/client";
import { keys, useApiMutation, useMeta, useProfiles, useProjects } from "../api/queries";
import { label, options } from "../labels";
import {
  Badge,
  Button,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
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

function ProjectDialog({ project, onClose }: { project: Project | null; onClose: () => void }) {
  const meta = useMeta();
  const profiles = useProfiles();
  const [name, setName] = useState(project?.name ?? "");
  const [engine, setEngine] = useState<Engine>(project?.engine ?? "unity");
  const [path, setPath] = useState(project?.path ?? "");
  const [defaultProfile, setDefaultProfile] = useState(project?.default_profile_id ?? "");
  const save = useApiMutation(
    () => {
      const input = { name: name.trim(), engine, path: path.trim(), default_profile_id: defaultProfile || null };
      return project ? api.updateProject(project.id, input) : api.createProject(input);
    },
    [keys.projects, keys.profiles],
  );
  const choices = (profiles.data ?? []).filter((p) => p.project_id === (project?.id ?? null) || p.project_id === null);

  return (
    <Dialog
      open
      title={project ? `Edit ${project.name}` : "Add a project"}
      onClose={onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim()} busy={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onClose })}>
            {project ? "Save" : "Add project"}
          </Button>
        </>
      }
    >
      <Stack gap={4}>
        <Field label="Name">
          {(id) => <Input id={id} autoFocus value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Shadowforge Legends" />}
        </Field>
        <Field label="Engine">
          {(id) => <Select id={id} value={engine} onChange={(e) => setEngine(e.target.value as Engine)} options={options(meta.data?.engines ?? ["unity", "godot"])} />}
        </Field>
        <Field
          label="Project folder"
          hint={engine === "unity" ? "The Unity project root. Exports go to Assets/Generated/…" : "The Godot project root (res://). Exports go to generated/…"}
        >
          {(id) => <Input id={id} value={path} onChange={(e) => setPath(e.target.value)} placeholder={engine === "unity" ? "D:\\Games\\MyRpg" : "D:\\Games\\IdleRpg"} mono />}
        </Field>
        <Field label="Default style profile" hint={project ? undefined : "Leave it to have a Default profile made for the project."}>
          {(id) => (
            <Select
              id={id}
              value={defaultProfile}
              onChange={(e) => setDefaultProfile(e.target.value)}
              placeholder={project ? "None" : "Make a new Default profile"}
              options={choices.map((p) => ({ value: p.id, label: p.name }))}
            />
          )}
        </Field>
        <ErrorNotice error={save.error} />
      </Stack>
    </Dialog>
  );
}

export function ProjectsPage() {
  const projects = useProjects();
  const profiles = useProfiles();
  const [editing, setEditing] = useState<Project | "new" | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const remove = useApiMutation((id: string) => api.deleteProject(id), [keys.projects, keys.profiles]);
  const profileName = (id: string | null) => profiles.data?.find((p) => p.id === id)?.name ?? "—";

  return (
    <Stack gap={4}>
      <PageHeader
        title="Projects"
        subtitle="The games assets are exported into."
        actions={<Button variant="primary" onClick={() => setEditing("new")}>Add project</Button>}
      />
      <ErrorNotice error={projects.error} />
      {projects.isPending ? (
        <Loading />
      ) : !projects.data?.length ? (
        <EmptyState
          title="No projects yet"
          body="Register your Unity or Godot game with its folder, and generated assets are written straight into it."
          action={<Button variant="primary" onClick={() => setEditing("new")}>Add project</Button>}
        />
      ) : (
        <Table head={["Name", "Engine", "Folder", "Default profile", ""]}>
          {projects.data.map((p) => (
            <TableRow key={p.id}>
              <td>
                <Text strong>{p.name}</Text>
              </td>
              <td>
                <Badge tone={p.engine === "unity" ? "info" : "success"}>{label(p.engine)}</Badge>
              </td>
              <td>{p.path ? <Text mono>{p.path}</Text> : <Text faint>Not set (zip download only)</Text>}</td>
              <td>{profileName(p.default_profile_id)}</td>
              <td>
                <Row justify="end">
                  <LinkButton to={`/?project=${p.id}`} size="sm" variant="ghost">Assets</LinkButton>
                  <Button size="sm" onClick={() => setEditing(p)}>Edit</Button>
                  <Button size="sm" variant="danger" onClick={() => { remove.reset(); setDeleting(p); }}>Delete</Button>
                </Row>
              </td>
            </TableRow>
          ))}
        </Table>
      )}
      {editing && <ProjectDialog project={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={`Delete ${deleting?.name}?`}
        body="Only a project without assets can be deleted. Its style profiles are kept, unassigned."
        confirmLabel="Delete project"
        busy={remove.isPending}
        error={remove.error}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })}
      />
    </Stack>
  );
}
