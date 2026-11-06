import { useState, type FormEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, localUrl, type Kind, type Meta, type PerKind, type Profile, type ProfileInput } from "../api/client";
import { keys, useApiMutation, useLoras, useMeta, useProfile, useProjects } from "../api/queries";
import { KIND_LABEL, label, options } from "../labels";
import {
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Field,
  Grid,
  Input,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Row,
  Select,
  Stack,
  Text,
  TextArea,
  Thumb,
} from "../ui/kit";

const KINDS: Kind[] = ["hero", "monster", "boss", "gear", "weapon"];
const list = (text: string) => text.split(",").map((t) => t.trim()).filter(Boolean);

function toInput(source: Profile | Meta["profile_defaults"], name: string, projectId: string | null): ProfileInput {
  return {
    name,
    project_id: projectId,
    base_model: source.base_model,
    prompt_suffix: source.prompt_suffix,
    negative_prompt: source.negative_prompt,
    loras: source.loras.map((l) => ({ ...l, weight: Math.round(l.weight * 100) / 100 })),
    camera_preset: source.camera_preset,
    delight: source.delight ?? true,
    candidates: source.candidates,
    clips: [...source.clips],
    biped_extra_clips: [...source.biped_extra_clips],
    render: { ...source.render, frame_size: { ...source.render.frame_size } },
    budgets: {
      triangles: { ...source.budgets.triangles },
      texture: { ...source.budgets.texture },
      lod_ratios: [...source.budgets.lod_ratios],
    },
  };
}

function NumberInput({ value, onChange, min, max, step, id, label: aria }: { value: number; onChange: (n: number) => void; min?: number; max?: number; step?: number; id?: string; label?: string }) {
  return (
    <Input
      id={id}
      aria-label={aria}
      name={id ?? aria}
      type="number"
      value={Number.isFinite(value) ? value : ""}
      min={min}
      max={max}
      step={step ?? 1}
      onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
    />
  );
}

function PerKindFields({ title, hint, values, onChange, min, max }: { title: string; hint?: string; values: PerKind; onChange: (v: PerKind) => void; min: number; max?: number }) {
  return (
    <Field label={title} hint={hint} group>
      {() => (
        <Grid cols={5}>
          {KINDS.map((k) => (
            <Stack key={k} gap={1}>
              <Text small faint>{KIND_LABEL[k]}</Text>
              <NumberInput label={`${title} ${k}`} value={values[k]} min={min} max={max} onChange={(n) => onChange({ ...values, [k]: n })} />
            </Stack>
          ))}
        </Grid>
      )}
    </Field>
  );
}

function ProfileForm({ initial, profile, meta }: { initial: ProfileInput; profile: Profile | null; meta: Meta }) {
  const navigate = useNavigate();
  const projects = useProjects();
  const allLoras = useLoras();
  const [form, setForm] = useState(initial);
  const [pending, setPending] = useState<{ file: File; preview: string }[]>([]);
  const [deleting, setDeleting] = useState(false);
  const set = <K extends keyof ProfileInput>(key: K, value: ProfileInput[K]) => setForm((f) => ({ ...f, [key]: value }));
  const compatible = (allLoras.data ?? []).filter((l) => l.base_model === form.base_model);
  const references = profile?.reference_images ?? [];
  const room = meta.max_reference_images - references.length - pending.length;

  const save = useApiMutation(async () => {
    if (profile) return api.updateProfile(profile.id, form);
    let created = await api.createProfile(form);
    for (const { file } of pending) created = await api.uploadReference(created.id, file);
    return created;
  }, profile ? [keys.profiles, keys.profile(profile.id)] : [keys.profiles]);
  const refreshed = profile ? [keys.profiles, keys.profile(profile.id)] : [keys.profiles];
  const upload = useApiMutation((file: File) => api.uploadReference(profile!.id, file), refreshed);
  const removeRef = useApiMutation((refId: string) => api.deleteReference(profile!.id, refId), refreshed);
  const remove = useApiMutation(() => api.deleteProfile(profile!.id), [keys.profiles]);

  const changeBaseModel = (base: ProfileInput["base_model"]) => {
    // LoRAs only work with their own base model; drop the others.
    const keep = new Set((allLoras.data ?? []).filter((l) => l.base_model === base).map((l) => l.id));
    setForm((f) => ({ ...f, base_model: base, loras: f.loras.filter((l) => keep.has(l.id)) }));
  };
  const toggleLora = (id: string, on: boolean) =>
    set("loras", on ? [...form.loras, { id, weight: 0.8 }] : form.loras.filter((l) => l.id !== id));
  const weight = (id: string, w: number) => set("loras", form.loras.map((l) => (l.id === id ? { ...l, weight: w } : l)));

  const numbersOk = [
    form.candidates,
    form.render.fps,
    form.render.supersample,
    form.render.palette_size,
    ...Object.values(form.render.frame_size),
    ...Object.values(form.budgets.triangles),
    ...Object.values(form.budgets.texture),
    ...form.loras.map((l) => l.weight),
  ].every(Number.isFinite);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(undefined, {
      onSuccess: (saved) => {
        setPending([]);
        if (!profile) navigate(`/profiles/${saved.id}`, { replace: true });
      },
    });
  };

  const addFiles = (files: FileList | null) => {
    const chosen = Array.from(files ?? []).slice(0, Math.max(0, room));
    if (profile) chosen.forEach((f) => upload.mutate(f));
    else setPending((p) => [...p, ...chosen.map((file) => ({ file, preview: URL.createObjectURL(file) }))]);
  };

  return (
    <form onSubmit={submit}>
      <Stack gap={4}>
        <PageHeader
          back={{ to: "/profiles", label: "Style profiles" }}
          title={profile ? profile.name : "New style profile"}
          actions={
            <>
              {profile && (
                <Button variant="ghost" onClick={() => { remove.reset(); setDeleting(true); }}>
                  Delete
                </Button>
              )}
              <Button type="submit" variant="primary" busy={save.isPending} disabled={!form.name.trim() || !numbersOk}>
                {profile ? "Save changes" : "Create profile"}
              </Button>
            </>
          }
        />
        <ErrorNotice error={save.error} />
        {save.isSuccess && profile && <Notice tone="success" title="Saved" />}

        <Card title="General">
          <Grid cols={2}>
            <Field label="Name">
              {(id) => <Input id={id} value={form.name} maxLength={80} onChange={(e) => set("name", e.target.value)} required />}
            </Field>
            <Field label="Project">
              {(id) => (
                <Select
                  id={id}
                  value={form.project_id ?? ""}
                  onChange={(e) => set("project_id", e.target.value || null)}
                  placeholder="No project"
                  options={(projects.data ?? []).map((p) => ({ value: p.id, label: `${p.name} (${label(p.engine)})` }))}
                />
              )}
            </Field>
          </Grid>
        </Card>

        <Card title="Image model" subtitle="Used for the gate 1 previews.">
          <Stack gap={4}>
            <Grid cols={2}>
              <Field label="Base model" hint="Changing it removes LoRAs made for another base model.">
                {(id) => <Select id={id} value={form.base_model} onChange={(e) => changeBaseModel(e.target.value as ProfileInput["base_model"])} options={options(meta.base_models)} />}
              </Field>
              <Field label="Preview candidates" hint="Images rendered per round at gate 1.">
                {(id) => <NumberInput id={id} value={form.candidates} min={1} max={8} onChange={(n) => set("candidates", n)} />}
              </Field>
            </Grid>
            <Field label="Prompt suffix" hint="Added to every expanded prompt.">
              {(id) => <TextArea id={id} rows={2} value={form.prompt_suffix} onChange={(e) => set("prompt_suffix", e.target.value)} />}
            </Field>
            <Field label="Negative prompt">
              {(id) => <TextArea id={id} rows={2} value={form.negative_prompt} onChange={(e) => set("negative_prompt", e.target.value)} />}
            </Field>
          </Stack>
        </Card>

        <Card title="LoRAs" subtitle={`Only LoRAs for ${label(form.base_model)} are offered.`} actions={<LinkButton to="/loras" size="sm" variant="ghost">LoRA library</LinkButton>}>
          {compatible.length === 0 ? (
            <Text muted>No {label(form.base_model)} LoRAs in the library yet.</Text>
          ) : (
            <Stack gap={2}>
              {compatible.map((l) => {
                const entry = form.loras.find((e) => e.id === l.id);
                return (
                  <Row key={l.id} gap={3} justify="between">
                    <Row gap={2}>
                      <Checkbox checked={!!entry} onChange={(on) => toggleLora(l.id, on)} label={l.name} />
                      {l.tags.map((t) => <Badge key={t}>{t}</Badge>)}
                      {!l.commercial_use && <Badge tone="warning">Not for commercial use</Badge>}
                    </Row>
                    {entry && (
                      <Box width={120}>
                        <NumberInput label={`${l.name} weight`} value={entry.weight} min={-2} max={2} step={0.05} onChange={(w) => weight(l.id, w)} />
                      </Box>
                    )}
                  </Row>
                );
              })}
            </Stack>
          )}
        </Card>

        <Card title="Reference images" subtitle={`Up to ${meta.max_reference_images}. They steer the style of the previews.`}>
          <Stack gap={3}>
            <Grid min={140}>
              {references.map((r) => (
                <Stack key={r.id} gap={1}>
                  <Thumb src={localUrl(r.url)} alt={r.filename} />
                  <Row justify="between">
                    <Text small muted>{r.filename}</Text>
                    <Button size="sm" variant="ghost" busy={removeRef.isPending && removeRef.variables === r.id} onClick={() => removeRef.mutate(r.id)}>
                      Remove
                    </Button>
                  </Row>
                </Stack>
              ))}
              {pending.map(({ file, preview }, i) => (
                <Stack key={preview} gap={1}>
                  <Thumb src={preview} alt={file.name} />
                  <Row justify="between">
                    <Text small muted>{file.name} (uploads on create)</Text>
                    <Button size="sm" variant="ghost" onClick={() => setPending((p) => p.filter((_, j) => j !== i))}>Remove</Button>
                  </Row>
                </Stack>
              ))}
            </Grid>
            {room > 0 ? (
              <Field label="Add images" hint="PNG, JPEG or WebP.">
                {(id) => <Input id={id} key={references.length + pending.length} type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={(e) => addFiles(e.target.files)} />}
              </Field>
            ) : (
              <Text small faint>The profile has the most reference images it can hold.</Text>
            )}
            <ErrorNotice error={upload.error ?? removeRef.error} />
          </Stack>
        </Card>

        <Card title="Mesh" subtitle="Used for the gate 2 mesh from Hunyuan3D.">
          <Field group label="Lighting" hint="Hunyuan3D bakes light and shade into the texture. This evens out its brightness so the game's own lights do the shading. Turn it off and reject at gate 2 to compare.">
            {() => <Checkbox checked={form.delight} onChange={(on) => set("delight", on)} label="Even out baked lighting" />}
          </Field>
        </Card>

        <Card title="Camera and sprite render" subtitle="Used for Godot sprite sheets.">
          <Stack gap={4}>
            <Grid cols={3}>
              <Field label="Camera preset" hint={`Directions: ${meta.camera_directions[form.camera_preset].map(label).join(", ")}`}>
                {(id) => <Select id={id} value={form.camera_preset} onChange={(e) => set("camera_preset", e.target.value as ProfileInput["camera_preset"])} options={options(meta.camera_presets)} />}
              </Field>
              <Field label="Look">
                {(id) => <Select id={id} value={form.render.look} onChange={(e) => set("render", { ...form.render, look: e.target.value as "hd" | "pixel" })} options={options(meta.sprite_looks)} />}
              </Field>
              <Field label="Frames per second">
                {(id) => <NumberInput id={id} value={form.render.fps} min={1} max={60} onChange={(n) => set("render", { ...form.render, fps: n })} />}
              </Field>
              <Field label="Supersampling" hint="Render at N× and scale down.">
                {(id) => <NumberInput id={id} value={form.render.supersample} min={1} max={4} onChange={(n) => set("render", { ...form.render, supersample: n })} />}
              </Field>
              <Field label="Palette size" hint="Pixel look only.">
                {(id) => <NumberInput id={id} value={form.render.palette_size} min={2} max={256} onChange={(n) => set("render", { ...form.render, palette_size: n })} />}
              </Field>
            </Grid>
            <PerKindFields title="Frame size (px)" values={form.render.frame_size} min={16} max={2048} onChange={(v) => set("render", { ...form.render, frame_size: v })} />
          </Stack>
        </Card>

        <Card title="Budgets">
          <Stack gap={4}>
            <PerKindFields title="Triangles" hint="LOD0 budget per kind." values={form.budgets.triangles} min={1} onChange={(v) => set("budgets", { ...form.budgets, triangles: v })} />
            <PerKindFields title="Texture size (px)" values={form.budgets.texture} min={1} onChange={(v) => set("budgets", { ...form.budgets, texture: v })} />
            <Field label="Extra LOD ratios" hint="Comma-separated fractions of LOD0, e.g. 0.5, 0.2">
              {(id) => (
                <Input
                  id={id}
                  defaultValue={form.budgets.lod_ratios.join(", ")}
                  onChange={(e) => set("budgets", { ...form.budgets, lod_ratios: list(e.target.value).map(Number).filter((n) => n > 0 && n < 1) })}
                />
              )}
            </Field>
          </Stack>
        </Card>

        <Card title="Clips">
          <Grid cols={2}>
            <Field label="Clip set" hint="Comma-separated.">
              {(id) => <Input id={id} defaultValue={form.clips.join(", ")} onChange={(e) => set("clips", list(e.target.value))} />}
            </Field>
            <Field label="Extra clips for bipeds" hint="Comma-separated.">
              {(id) => <Input id={id} defaultValue={form.biped_extra_clips.join(", ")} onChange={(e) => set("biped_extra_clips", list(e.target.value))} />}
            </Field>
          </Grid>
        </Card>

        <Row justify="end">
          <Button type="submit" variant="primary" busy={save.isPending} disabled={!form.name.trim() || !numbersOk}>
            {profile ? "Save changes" : "Create profile"}
          </Button>
        </Row>
      </Stack>
      {profile && (
        <ConfirmDialog
          open={deleting}
          title={`Delete ${profile.name}?`}
          body="A project's default profile can't be deleted; pick another default for the project first."
          confirmLabel="Delete profile"
          busy={remove.isPending}
          error={remove.error}
          onClose={() => setDeleting(false)}
          onConfirm={() => remove.mutate(undefined, { onSuccess: () => navigate("/profiles") })}
        />
      )}
    </form>
  );
}

export function ProfileEditPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const meta = useMeta();
  const profile = useProfile(id);

  if (meta.isPending || (id && profile.isPending)) return <Loading />;
  if (!meta.data) return <ErrorNotice error={meta.error} />;
  if (id && !profile.data)
    return <EmptyState title="Profile not found" action={<LinkButton to="/profiles">Back to profiles</LinkButton>} />;

  const initial = profile.data
    ? toInput(profile.data, profile.data.name, profile.data.project_id)
    : toInput(meta.data.profile_defaults, "", params.get("project"));
  // Key by id so switching profiles starts a fresh form.
  return <ProfileForm key={profile.data?.id ?? "new"} initial={initial} profile={profile.data ?? null} meta={meta.data} />;
}
