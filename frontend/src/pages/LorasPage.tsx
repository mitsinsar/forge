import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, exampleUrl, type BaseModel, type Lora } from "../api/client";
import { keys, useApiMutation, useLatestJob, useLoras, useMeta } from "../api/queries";
import { ExampleSheet, JobProgress } from "../components/Examples";
import { label, options } from "../labels";
import {
  Badge,
  Box,
  Button,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNotice,
  ExternalLink,
  Field,
  Grid,
  Input,
  Loading,
  MediaCard,
  PageHeader,
  Row,
  SectionTitle,
  Select,
  Stack,
  Table,
  TableRow,
  Tabs,
  Text,
} from "../ui/kit";

const splitTags = (text: string) => text.split(",").map((t) => t.trim()).filter(Boolean);

/** A LoRA's first example (or your first image), for the table. */
const loraPicture = (l: Lora) => l.examples?.items[0]?.with ?? l.example_images?.[0];

function ExamplesDialog({ lora, onClose }: { lora: Lora; onClose: () => void }) {
  const client = useQueryClient();
  const reload = () => client.invalidateQueries({ queryKey: keys.loras });
  const job = useLatestJob(lora.id, "lora_examples", reload);
  const [weight, setWeight] = useState(String(lora.examples?.weight ?? 0.8));
  const make = useApiMutation((w: number) => api.loraExamples(lora.id, w), [keys.jobs(lora.id, "lora_examples")]);
  const add = useApiMutation((file: File) => api.addLoraImage(lora.id, file), [keys.loras]);
  const remove = useApiMutation((name: string) => api.removeLoraImage(lora.id, name), [keys.loras]);
  const busy = job.data?.status === "queued" || job.data?.status === "running";
  return (
    <Dialog open title={`Examples · ${lora.name}`} onClose={onClose} wide>
      <Stack gap={4}>
        <Text small muted>
          A knight, a wolf and a sword on {label(lora.base_model)}, with and without this LoRA. About half a minute
          per image; the worker runs it between asset stages.
        </Text>
        <Row align="top">
          <Box width={140}>
            <Field label="Weight">
              {(id) => <Input id={id} type="number" min={0} max={2} step={0.1} value={weight} onChange={(e) => setWeight(e.target.value)} />}
            </Field>
          </Box>
          <Button variant="primary" busy={make.isPending || busy} disabled={!lora.file_exists}
                  onClick={() => make.mutate(Number(weight) || 0.8)}>
            {lora.examples ? "Make new examples" : "Make examples"}
          </Button>
        </Row>
        <JobProgress job={job.data} />
        <ErrorNotice error={make.error} />
        <ExampleSheet examples={lora.examples} compare />
        <SectionTitle>Your images</SectionTitle>
        <Grid min={140}>
          {(lora.example_images ?? []).map((path) => (
            <Stack key={path} gap={1}>
              <MediaCard image={exampleUrl(path)} title="Your image" />
              <Button size="sm" variant="ghost" onClick={() => remove.mutate(path.split("/").pop()!)}>Remove</Button>
            </Stack>
          ))}
        </Grid>
        <Field label="Add an image" hint="PNG, JPEG or WebP: something you made with this LoRA, or its page's samples.">
          {(id) => (
            <Input id={id} type="file" accept="image/png,image/jpeg,image/webp"
                   onChange={(e) => e.target.files?.[0] && add.mutate(e.target.files[0])} />
          )}
        </Field>
        <ErrorNotice error={add.error ?? remove.error} />
      </Stack>
    </Dialog>
  );
}

function LoraDialog({ lora, onClose }: { lora: Lora | null; onClose: () => void }) {
  const meta = useMeta();
  const [mode, setMode] = useState<"upload" | "path">(lora ? "path" : "upload");
  const [file, setFile] = useState<File | null>(null);
  const [path, setPath] = useState(lora?.path ?? "");
  const [name, setName] = useState(lora?.name ?? "");
  const [baseModel, setBaseModel] = useState<BaseModel>(lora?.base_model ?? "sdxl");
  const [tags, setTags] = useState(lora?.tags.join(", ") ?? "");
  const [licence, setLicence] = useState(lora?.licence ?? "");
  const [commercial, setCommercial] = useState(lora?.commercial_use ?? false);
  const [source, setSource] = useState(lora?.source_url ?? "");
  const [trigger, setTrigger] = useState(lora?.trigger ?? "");

  const save = useApiMutation(() => {
    const fields = {
      name: name.trim() || file?.name.replace(/\.[^.]+$/, "") || "",
      base_model: baseModel,
      tags: splitTags(tags),
      licence: licence.trim(),
      commercial_use: commercial,
      source_url: source.trim(),
      trigger: trigger.trim(),
    };
    if (lora) return api.updateLora(lora.id, lora.uploaded ? fields : { ...fields, path: path.trim() });
    if (mode === "upload") return api.uploadLora(file!, fields);
    return api.registerLora({ ...fields, path: path.trim() });
  }, [keys.loras, keys.profiles]);

  const ready = lora ? !!name.trim() : mode === "upload" ? !!file : !!path.trim() && !!name.trim();

  return (
    <Dialog
      open
      wide
      title={lora ? `Edit ${lora.name}` : "Add a LoRA"}
      onClose={onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready} busy={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onClose })}>
            {lora ? "Save" : mode === "upload" ? "Upload" : "Register"}
          </Button>
        </>
      }
    >
      <Stack gap={4}>
        {!lora && (
          <Tabs
            label="How to add it"
            value={mode}
            onChange={setMode}
            items={[
              { value: "upload", label: "Upload a file" },
              { value: "path", label: "Register a file path" },
            ]}
          />
        )}
        {!lora && mode === "upload" && (
          <Field label="LoRA file" hint=".safetensors, .ckpt or .pt">
            {(id) => <Input id={id} type="file" accept=".safetensors,.ckpt,.pt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
          </Field>
        )}
        {(mode === "path" && !lora?.uploaded) && (
          <Field label="File path" hint="Where the file is on the machine that runs ComfyUI.">
            {(id) => <Input id={id} mono value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\ComfyUI\models\loras\dnd-painterly.safetensors" />}
          </Field>
        )}
        <Grid cols={2}>
          <Field label="Name" hint={mode === "upload" && !lora ? "Defaults to the file name." : undefined}>
            {(id) => <Input id={id} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <Field label="Base model" hint="Profiles only offer LoRAs of their own base model.">
            {(id) => <Select id={id} value={baseModel} onChange={(e) => setBaseModel(e.target.value as BaseModel)} options={options(meta.data?.base_models ?? [])} />}
          </Field>
          <Field label="Tags" hint="Comma-separated.">
            {(id) => <Input id={id} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="painterly, fantasy" />}
          </Field>
          <Field label="Licence">
            {(id) => <Input id={id} value={licence} onChange={(e) => setLicence(e.target.value)} placeholder="CreativeML OpenRAIL-M" />}
          </Field>
        </Grid>
        <Field label="Trigger words" hint="The word(s) it was trained on, from its page (Civitai: “Trigger Words”). Added to the front of every prompt that uses it.">
          {(id) => <Input id={id} mono value={trigger} maxLength={200} onChange={(e) => setTrigger(e.target.value)} placeholder="hkstyle" />}
        </Field>
        <Field label="Source URL">
          {(id) => <Input id={id} type="url" value={source} onChange={(e) => setSource(e.target.value)} placeholder="https://civitai.com/models/…" />}
        </Field>
        <Checkbox checked={commercial} onChange={setCommercial} label="Commercial use OK (checked against the licence)" />
        <ErrorNotice error={save.error} />
      </Stack>
    </Dialog>
  );
}

export function LorasPage() {
  const loras = useLoras();
  const [editing, setEditing] = useState<Lora | "new" | null>(null);
  const [showing, setShowing] = useState<Lora | null>(null);
  const [deleting, setDeleting] = useState<Lora | null>(null);
  const remove = useApiMutation((id: string) => api.deleteLora(id), [keys.loras, keys.profiles]);

  return (
    <Stack gap={4}>
      <PageHeader
        title="LoRA library"
        subtitle="Style LoRAs, tagged with their base model and licence."
        actions={<Button variant="primary" onClick={() => setEditing("new")}>Add LoRA</Button>}
      />
      <ErrorNotice error={loras.error} />
      {loras.isPending ? (
        <Loading />
      ) : !loras.data?.length ? (
        <EmptyState
          title="No LoRAs yet"
          body="Upload a LoRA file or register one by its path, then add it to a style profile with the same base model."
          action={<Button variant="primary" onClick={() => setEditing("new")}>Add LoRA</Button>}
        />
      ) : (
        <Table head={["", "Name", "Base model", "Tags", "Licence", "File", ""]}>
          {loras.data.map((l) => (
            <TableRow key={l.id}>
              <td style={{ width: 72 }}>
                {loraPicture(l) ? (
                  <MediaCard image={exampleUrl(loraPicture(l)!)} title="" onClick={() => setShowing(l)} />
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => setShowing(l)}>No examples</Button>
                )}
              </td>
              <td>
                <Stack gap={1}>
                  <Text strong>{l.name}</Text>
                  {l.source_url && <ExternalLink href={l.source_url} small>Source</ExternalLink>}
                </Stack>
              </td>
              <td><Badge tone="info">{label(l.base_model)}</Badge></td>
              <td>
                <Row gap={1}>{l.tags.map((t) => <Badge key={t}>{t}</Badge>)}</Row>
              </td>
              <td>
                <Stack gap={1}>
                  <Text small>{l.licence || "—"}</Text>
                  {l.commercial_use ? <Badge tone="success">Commercial use OK</Badge> : <Badge tone="warning">Not for commercial use</Badge>}
                </Stack>
              </td>
              <td>
                <Stack gap={1}>
                  <Text small muted>{l.uploaded ? "Uploaded" : "Registered path"}</Text>
                  {!l.file_exists && <Badge tone="danger">File missing</Badge>}
                </Stack>
              </td>
              <td>
                <Row justify="end">
                  <Button size="sm" onClick={() => setShowing(l)}>Examples</Button>
                  <Button size="sm" onClick={() => setEditing(l)}>Edit</Button>
                  <Button size="sm" variant="danger" onClick={() => { remove.reset(); setDeleting(l); }}>Delete</Button>
                </Row>
              </td>
            </TableRow>
          ))}
        </Table>
      )}
      {editing && <LoraDialog lora={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {showing && (
        <ExamplesDialog lora={loras.data?.find((l) => l.id === showing.id) ?? showing} onClose={() => setShowing(null)} />
      )}
      <ConfirmDialog
        open={!!deleting}
        title={`Delete ${deleting?.name}?`}
        body={deleting?.uploaded ? "It is removed from every profile and the uploaded file is deleted." : "It is removed from every profile. The file itself stays where it is."}
        confirmLabel="Delete LoRA"
        busy={remove.isPending}
        error={remove.error}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })}
      />
    </Stack>
  );
}
