import { useState } from "react";
import { api, type Motion } from "../api/client";
import { keys, useApiMutation, useMotions, useProfiles } from "../api/queries";
import { MotionVersions } from "../components/MotionVersions";
import { ModelViewer } from "../components/Viewer";
import {
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNotice,
  Field,
  Grid,
  Input,
  Loading,
  Notice,
  PageHeader,
  ProgressBar,
  Row,
  Select,
  Stack,
  StageMessage,
  Table,
  TableRow,
  Text,
  TextArea,
} from "../ui/kit";

const busy = (m: Motion) => m.status === "queued" || m.status === "running";

function NewMotionDialog({ onClose, onMade }: { onClose: () => void; onMade: (m: Motion) => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState("");
  const [inPlace, setInPlace] = useState(true);
  const make = useApiMutation(
    () =>
      api.createMotion({
        title: title.trim(),
        description: description.trim(),
        prompt: prompt.trim() || null,
        duration: duration ? Number(duration) : null,
        in_place: inPlace,
      }),
    [keys.motions],
  );
  return (
    <Dialog
      open
      wide
      title="New skill animation"
      onClose={onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!title.trim() || !description.trim()} busy={make.isPending}
                  onClick={() => make.mutate(undefined, { onSuccess: (m) => { onMade(m); onClose(); } })}>
            Make it
          </Button>
        </>
      }
    >
      <Stack gap={4}>
        <Text small muted>
          HY-Motion animates the body only: the fireball or the arrow is a separate effect. The language model turns
          your words into a motion prompt; about a minute on the worker.
        </Text>
        <Field label="Skill name" hint="The clip is called skill_<name> in the exports.">
          {(id) => <Input id={id} value={title} placeholder="Firebolt" onChange={(e) => setTitle(e.target.value)} />}
        </Field>
        <Field label="What the character does">
          {(id) => (
            <TextArea id={id} rows={3} value={description} placeholder="Gathers fire in the right hand, then hurls it forward"
                      onChange={(e) => setDescription(e.target.value)} />
          )}
        </Field>
        <Row align="top">
          <Box width={160}>
            <Field label="Seconds" hint="Empty: guessed.">
              {(id) => <Input id={id} type="number" min={1} max={10} step={0.5} value={duration} onChange={(e) => setDuration(e.target.value)} />}
            </Field>
          </Box>
          <Checkbox checked={inPlace} onChange={setInPlace} label="In place (no travel along the ground)" />
        </Row>
        <Field label="Your own motion prompt (optional)" hint={'English, "A person ..." and what the limbs do, under 60 words. Used as it is.'}>
          {(id) => <TextArea id={id} rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} />}
        </Field>
        <ErrorNotice error={make.error} />
      </Stack>
    </Dialog>
  );
}

function MotionDetail({ motion }: { motion: Motion }) {
  const profiles = useProfiles();
  const [note, setNote] = useState("");
  const [profileId, setProfileId] = useState("");
  const [deleting, setDeleting] = useState(false);
  const redo = useApiMutation((n: string) => api.redoMotion(motion.id, { note: n }), [keys.motions]);
  const use = useApiMutation((id: string) => api.useMotion(motion.id, id), [keys.profiles]);
  const remove = useApiMutation(() => api.deleteMotion(motion.id), [keys.motions]);
  const used = (profiles.data ?? []).filter((p) => p.biped_extra_clips.includes(motion.name));
  return (
    <Card
      title={motion.title}
      subtitle={<span className="mono">{motion.name}</span>}
      actions={<Button size="sm" variant="ghost" onClick={() => setDeleting(true)}>Delete</Button>}
    >
      <Stack gap={4}>
        {motion.url ? (
          <ModelViewer url={motion.url} clip={motion.name} />
        ) : busy(motion) ? (
          <StageMessage>
            <Stack gap={2}>
              <Text small>{motion.step}</Text>
              <ProgressBar value={motion.progress} label={motion.step} />
            </Stack>
          </StageMessage>
        ) : (
          <StageMessage>No preview</StageMessage>
        )}
        {motion.status === "failed" && <Notice tone="danger" title="It failed">{motion.error}</Notice>}
        <Text small muted>{motion.description}</Text>
        {motion.prompt && (
          <Text small>
            <strong>Motion prompt:</strong> {motion.prompt}
            {motion.clip ? ` (${motion.clip.duration.toFixed(1)} s)` : ""}
          </Text>
        )}
        <MotionVersions motion={motion} />
        <Row align="top">
          <Box minWidth={260}>
            <Field label="Make it again" hint="What to change; the prompt is written again with it.">
              {(id) => <Input id={id} value={note} placeholder="Use the left hand, slower" onChange={(e) => setNote(e.target.value)} />}
            </Field>
          </Box>
          <Button disabled={busy(motion)} busy={redo.isPending} onClick={() => redo.mutate(note, { onSuccess: () => setNote("") })}>
            {note.trim() ? "Make again with the note" : "Make again (new seed)"}
          </Button>
        </Row>
        <Row align="top">
          <Box minWidth={260}>
            <Field label="Give it to the bipeds of a style profile" hint="Heroes rigged from now on get it; rigged ones when rigged again.">
              {(id) => (
                <Select id={id} value={profileId} placeholder="Pick a profile" onChange={(e) => setProfileId(e.target.value)}
                        options={(profiles.data ?? []).map((p) => ({ value: p.id, label: p.name }))} />
              )}
            </Field>
          </Box>
          <Button disabled={!profileId || motion.status !== "done"} busy={use.isPending} onClick={() => use.mutate(profileId)}>
            Add the clip
          </Button>
        </Row>
        {used.length > 0 && (
          <Row>
            <Text small muted>In:</Text>
            {used.map((p) => <Badge key={p.id} tone="accent">{p.name}</Badge>)}
          </Row>
        )}
        <ErrorNotice error={redo.error ?? use.error} />
      </Stack>
      <ConfirmDialog open={deleting} title={`Delete ${motion.title}?`} confirmLabel="Delete" busy={remove.isPending}
                     error={remove.error}
                     body="The clip file goes too. Profiles that list it fail to rig until you take it out of their clips."
                     onConfirm={() => remove.mutate(undefined, { onSuccess: () => setDeleting(false) })}
                     onClose={() => setDeleting(false)} />
    </Card>
  );
}

export function SkillsPage() {
  const motions = useMotions();
  const [creating, setCreating] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const list = motions.data ?? [];
  const current = list.find((m) => m.id === picked) ?? list[list.length - 1];
  return (
    <Stack gap={5}>
      <PageHeader
        title="Skills"
        subtitle="Skill animations: a description becomes a body animation (HY-Motion) your bipeds can play."
        actions={<Button variant="primary" onClick={() => setCreating(true)}>New skill animation</Button>}
      />
      {motions.isLoading ? (
        <Loading />
      ) : list.length === 0 ? (
        <EmptyState title="No skill animations yet" body="Describe a skill, like a firebolt or a healing prayer."
                    action={<Button variant="primary" onClick={() => setCreating(true)}>New skill animation</Button>} />
      ) : (
        <Grid cols={2}>
          <Table head={["Skill", "Clip", "State"]}>
            {list.map((m) => (
              <TableRow key={m.id} onClick={() => setPicked(m.id)}>
                <td>{current?.id === m.id ? <strong>{m.title}</strong> : m.title}</td>
                <td className="mono">{m.name}</td>
                <td>
                  <Badge tone={m.status === "done" ? "success" : m.status === "failed" ? "danger" : "info"} dot pulse={busy(m)}>
                    {m.status === "done" ? `${m.clip?.duration.toFixed(1) ?? "?"} s` : m.status}
                  </Badge>
                </td>
              </TableRow>
            ))}
          </Table>
          {current && <MotionDetail key={current.id} motion={current} />}
        </Grid>
      )}
      <ErrorNotice error={motions.error} />
      {creating && <NewMotionDialog onClose={() => setCreating(false)} onMade={(m) => setPicked(m.id)} />}
    </Stack>
  );
}
