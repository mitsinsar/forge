import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type AssetInput, type Kind, type Slot, type WeaponClass } from "../api/client";
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
  Text,
  TextArea,
  Thumb,
} from "../ui/kit";

type Start = "describe" | "image";

/** An object URL for a picked file, revoked when the file changes or the page closes. */
function usePreviewUrl(file: File | null): string | null {
  const url = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  return url;
}

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
  const [start, setStart] = useState<Start>("describe");
  const [image, setImage] = useState<File | null>(null);
  const preview = usePreviewUrl(image);

  const create = useApiMutation(
    (input: AssetInput) => (start === "image" && image ? api.createAssetFromImage(input, image) : api.createAsset(input)),
    [keys.assets],
  );
  const fromImage = start === "image";

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
        <PageHeader title="New asset" subtitle="Describe it, or bring your own image of it." back={{ to: "/", label: "Assets" }} />
        <Card>
          <Stack gap={4}>
            <Field
              label="Start from"
              group
              hint={
                fromImage
                  ? "The mesh is built straight from your image; no previews are generated."
                  : "The generator renders previews for you to pick from."
              }
            >
              {() => (
                <Tabs
                  label="Start from"
                  value={start}
                  onChange={setStart}
                  items={[
                    { value: "describe", label: "Description" },
                    { value: "image", label: "My own image" },
                  ]}
                />
              )}
            </Field>
            {fromImage && (
              <Grid cols={2}>
                <Field label="Image" hint="PNG, JPEG or WebP. One subject, whole and uncropped, on a plain background works best.">
                  {(id) => (
                    <Input
                      id={id}
                      type="file"
                      required
                      accept="image/png,image/jpeg,image/webp"
                      onChange={(e) => setImage(e.target.files?.[0] ?? null)}
                    />
                  )}
                </Field>
                {preview ? <Thumb src={preview} alt="Your image" /> : <Text small faint>No image picked yet.</Text>}
              </Grid>
            )}
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
            <Field
              label="Description"
              hint={
                fromImage
                  ? "A few words on what it is. They name the asset and pick the archetype when it is automatic."
                  : "What it looks like. Colours, materials and silhouette help most."
              }
            >
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
              <Button
                type="submit"
                variant="primary"
                busy={create.isPending}
                disabled={!description.trim() || (fromImage && !image)}
              >
                {fromImage ? "Build from my image" : "Start generating"}
              </Button>
            </Row>
          </Stack>
        </Card>
      </Stack>
    </form>
  );
}
