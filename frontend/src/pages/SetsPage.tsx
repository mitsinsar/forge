import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CharacterSet } from "../api/client";
import { keys, useApiMutation, useProjects, useSets } from "../api/queries";
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  Stack,
  Table,
  TableRow,
  Text,
  TextArea,
} from "../ui/kit";

export const SET_STATUS: Record<CharacterSet["status"], { label: string; tone: "neutral" | "info" | "success" | "danger" | "warning" }> = {
  open: { label: "Picking", tone: "neutral" },
  changed: { label: "Changed since built", tone: "warning" },
  building: { label: "Building", tone: "info" },
  built: { label: "Built", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

function example(projectId: string) {
  return JSON.stringify({
    project_id: projectId,
    name: "Ember Mage",
    variations: 3,
    character: { description: "a young fire mage in red and gold robes, hood down, leather boots" },
    items: [{ kind: "weapon", weapon_class: "staff", description: "a gnarled wooden staff with a glowing ember at the top" }],
    skills: [
      { name: "Firebolt", description: "hurls a bolt of fire from the right hand",
        effect: "a fiery bolt with a bright core and a trail of embers", animation: "thrusts the right palm forward and hurls a firebolt" },
      { name: "Flame Ward", description: "a ring of fire swirls around the caster", animation: "raises both arms and spins once" },
    ],
  }, null, 2);
}

function NewSetDialog({ onClose }: { onClose: () => void }) {
  const projects = useProjects();
  const navigate = useNavigate();
  const [text, setText] = useState(() => example(projects.data?.[0]?.id ?? "<project id>"));
  const [parseError, setParseError] = useState<string | null>(null);
  const make = useApiMutation((spec: unknown) => api.createSet(spec), [keys.sets, keys.assets, keys.motions]);
  const submit = () => {
    let spec: unknown;
    try {
      spec = JSON.parse(text);
    } catch (error) {
      setParseError(`Not valid JSON: ${(error as Error).message}`);
      return;
    }
    setParseError(null);
    make.mutate(spec, { onSuccess: (made) => navigate(`/sets/${made.id}`) });
  };
  return (
    <Dialog open wide title="New character set" onClose={onClose}
            actions={
              <>
                <Button variant="ghost" onClick={onClose}>Cancel</Button>
                <Button variant="primary" busy={make.isPending} onClick={submit}>Make the versions</Button>
              </>
            }>
      <Stack gap={3}>
        <Text small muted>
          The same JSON as <code>POST /api/character-sets</code>. Forge makes <code>variations</code> versions of the hero,
          each item and each skill's effect and animation, on autopilot up to gate 3. Then pick one of each and build.
          The language model writes the varied descriptions first, which takes a little while.
        </Text>
        <TextArea rows={22} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        {parseError && <Text small>{parseError}</Text>}
        {make.error && <ErrorNotice error={make.error} />}
      </Stack>
    </Dialog>
  );
}

export function SetsPage() {
  const sets = useSets();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  if (sets.isLoading) return <Loading />;
  return (
    <Stack gap={5}>
      <PageHeader title="Character sets" subtitle="A hero, its weapons and gear, and its skills: versions of each, one picked, built together."
                  actions={<Button variant="primary" onClick={() => setCreating(true)}>New character set</Button>} />
      {sets.error && <ErrorNotice error={sets.error} />}
      {!sets.data?.length ? (
        <EmptyState title="No character sets yet" body="Send the JSON to POST /api/character-sets, or start one here."
                    action={<Button variant="primary" onClick={() => setCreating(true)}>New character set</Button>} />
      ) : (
        <Table head={["Set", "Versions", "Skills", "State"]}>
          {sets.data.map((s) => (
            <TableRow key={s.id} onClick={() => navigate(`/sets/${s.id}`)}>
              <td><strong>{s.name}</strong></td>
              <td>{s.variations} each</td>
              <td>{s.slots.skills.map((k) => k.name).join(", ") || "None"}</td>
              <td><Badge tone={SET_STATUS[s.status].tone}>{SET_STATUS[s.status].label}</Badge></td>
            </TableRow>
          ))}
        </Table>
      )}
      {creating && <NewSetDialog onClose={() => setCreating(false)} />}
    </Stack>
  );
}
