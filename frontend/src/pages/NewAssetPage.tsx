import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type Kind, type Slot, type WeaponClass } from "../api/client";
import { keys, useApiMutation, useArchetypes, useMeta, useProfiles, useProjects } from "../api/queries";
import { KIND_LABEL, isCharacter, label, options } from "../labels";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Field,
  Grid,
  Input,
  LinkButton,
  Loading,
  PageHeader,
  Row,
  Select,
  Stack,
  Tabs,
  TextArea,
} from "../ui/kit";

export function NewAssetPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const meta = useMeta();
  const projects = useProjects();
  const profiles = useProfiles();
  const archetypes = useArchetypes();

  const [projectChoice, setProjectId] = useState(params.get("project") ?? "");
  const [kind, setKind] = useState<Kind>("hero");
  const [description, setDescription] = useState("");
  const [name, setName] = useState("");
  const [archetype, setArchetype] = useState("");
  const [slot, setSlot] = useState<Slot>("chest");
  const [weaponClass, setWeaponClass] = useState<WeaponClass | "">("");
  const [profileChoice, setProfileId] = useState<string | null>(null);
  const [triangles, setTriangles] = useState("");

  const create = useApiMutation(api.createAsset, [keys.assets]);

  const projectId = projectChoice || projects.data?.[0]?.id || "";
  const project = projects.data?.find((p) => p.id === projectId);
  const projectProfiles = (profiles.data ?? []).filter((p) => p.project_id === projectId || p.project_id === null);
  const profileId = profileChoice ?? project?.default_profile_id ?? "";
  const profileTriangles = projectProfiles.find((p) => p.id === profileId)?.budgets.triangles[kind]
    ?? meta.data?.profile_defaults.budgets.triangles[kind];
  const triangleLimit = Number.parseInt(triangles, 10);

  if (projects.isPending || meta.isPending) return <Loading />;
  if (!projects.data?.length)
    return (
      <EmptyState
        title="Register a game project first"
        body="Every asset is generated for a Unity or Godot project."
        action={<LinkButton to="/projects" variant="primary">Add a project</LinkButton>}
      />
    );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        project_id: projectId,
        kind,
        description: description.trim(),
        name: name.trim() || undefined,
        archetype: isCharacter(kind) && archetype ? archetype : undefined,
        slot: kind === "gear" ? slot : undefined,
        weapon_class: kind === "weapon" && weaponClass ? weaponClass : undefined,
        profile_id: profileId || undefined,
        overrides: triangleLimit > 0 ? { budgets: { triangles: { [kind]: triangleLimit } } } : undefined,
      },
      { onSuccess: (asset) => navigate(`/asset/${asset.id}`) },
    );
  };

  return (
    <form onSubmit={submit}>
      <Stack gap={4}>
        <PageHeader title="New asset" subtitle="Describe it; the generator renders previews for you to pick from." back={{ to: "/", label: "Assets" }} />
        <Card>
          <Stack gap={4}>
            <Field label="Kind" group>
              {() => (
                <Tabs
                  label="Kind"
                  value={kind}
                  onChange={setKind}
                  items={(meta.data?.kinds ?? []).map((k) => ({ value: k, label: KIND_LABEL[k] }))}
                />
              )}
            </Field>
            <Field label="Description" hint="What it looks like. Colours, materials and silhouette help most.">
              {(id) => (
                <TextArea
                  id={id}
                  required
                  rows={4}
                  maxLength={2000}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={
                    kind === "weapon"
                      ? "a curved bronze sword with a leather-wrapped hilt"
                      : kind === "gear"
                        ? "a dented iron chestplate with a red tabard"
                        : "a knight in ember-red plate armour with a horned helmet"
                  }
                />
              )}
            </Field>
            <Grid cols={2}>
              <Field label="Project">
                {(id) => (
                  <Select
                    id={id}
                    value={projectId}
                    onChange={(e) => {
                      setProjectId(e.target.value);
                      setProfileId(null);
                    }}
                    options={projects.data.map((p) => ({ value: p.id, label: `${p.name} (${label(p.engine)})` }))}
                  />
                )}
              </Field>
              <Field label="Name" hint="Optional. Taken from the description when left empty.">
                {(id) => <Input id={id} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />}
              </Field>
              {isCharacter(kind) ? (
                <Field label="Archetype" hint="Picked from the description unless you choose one.">
                  {(id) => (
                    <Select
                      id={id}
                      value={archetype}
                      onChange={(e) => setArchetype(e.target.value)}
                      placeholder="Automatic"
                      options={(archetypes.data ?? []).map((a) => ({ value: a.id, label: a.name }))}
                    />
                  )}
                </Field>
              ) : (
                <Field label="Archetype" hint="Gear and weapons are always made on the biped mannequin.">
                  {(id) => <Input id={id} value="Biped" disabled readOnly />}
                </Field>
              )}
              {kind === "gear" && (
                <Field label="Slot">
                  {(id) => (
                    <Select id={id} value={slot} onChange={(e) => setSlot(e.target.value as Slot)} options={options(meta.data?.gear_slots ?? [])} />
                  )}
                </Field>
              )}
              {kind === "weapon" && (
                <Field label="Weapon class" hint="Picks the attack clips.">
                  {(id) => (
                    <Select
                      id={id}
                      value={weaponClass}
                      onChange={(e) => setWeaponClass(e.target.value as WeaponClass | "")}
                      placeholder="Automatic"
                      options={options(meta.data?.weapon_classes ?? [])}
                    />
                  )}
                </Field>
              )}
              <Field label="Style profile" hint="Optional. Defaults to the project's default profile.">
                {(id) => (
                  <Select
                    id={id}
                    value={profileId}
                    onChange={(e) => setProfileId(e.target.value)}
                    placeholder="Project default"
                    options={projectProfiles.map((p) => ({
                      value: p.id,
                      label: p.id === project?.default_profile_id ? `${p.name} (default)` : p.name,
                    }))}
                  />
                )}
              </Field>
              <Field label="Triangle limit" hint="Optional. Leave empty to use the style profile's limit for this kind.">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    min={1}
                    step={100}
                    value={triangles}
                    onChange={(e) => setTriangles(e.target.value)}
                    placeholder={profileTriangles ? `${profileTriangles.toLocaleString()} (profile)` : undefined}
                  />
                )}
              </Field>
            </Grid>
            <ErrorNotice error={create.error} />
            <Row justify="end">
              <Button type="submit" variant="primary" busy={create.isPending} disabled={!description.trim()}>
                Start generating
              </Button>
            </Row>
          </Stack>
        </Card>
      </Stack>
    </form>
  );
}
