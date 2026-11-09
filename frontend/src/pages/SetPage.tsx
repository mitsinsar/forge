import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, localUrl, type Asset, type CharacterSet, type Motion, type SetPicksInput } from "../api/client";
import { keys, useApiMutation, useAssets, useMotions, useSet } from "../api/queries";
import { StatusBadge } from "../components/AssetStatus";
import { PartRedos, partsOf } from "../components/EffectGates";
import { MotionVersions } from "../components/MotionVersions";
import { SkillTry } from "../components/SkillTry";
import { ModelViewer } from "../components/Viewer";
import {
  Badge,
  Button,
  Card,
  CodeBlock,
  ConfirmDialog,
  ErrorNotice,
  Field,
  Grid,
  Input,
  Loading,
  MediaCard,
  Notice,
  PageHeader,
  Row,
  Stack,
  StageMessage,
  Text,
} from "../ui/kit";
import { SET_STATUS } from "./SetsPage";

/** Ready to be picked into a built set: at gate 3, or finished. */
const ready = (a: Asset) => (a.status === "waiting" && a.gate === 3) || a.status === "done";

function AssetOptions({ ids, assets, picked, onPick, frames = false }: {
  ids: string[];
  assets: Map<string, Asset>;
  picked: string | null;
  onPick: (id: string) => void;
  frames?: boolean;
}) {
  return (
    <Grid cols={4} min={150}>
      {ids.map((id) => {
        const asset = assets.get(id);
        if (!asset) return <MediaCard key={id} title="Deleted" />;
        const flipbook = frames ? partsOf(asset)[0]?.frames : undefined;
        return (
          <MediaCard key={id} image={localUrl(asset.urls?.thumb ?? asset.urls?.icon)} frames={flipbook}
                     title={asset.name} subtitle={<StatusBadge asset={asset} />} selected={picked === id}
                     onClick={ready(asset) ? () => onPick(id) : undefined}
                     badges={<Link to={`/asset/${id}`} onClick={(e) => e.stopPropagation()}>Open</Link>} />
        );
      })}
    </Grid>
  );
}

function MotionOptions({ ids, motions, picked, onPick, onShow, shown }: {
  ids: string[];
  motions: Map<string, Motion>;
  picked: string | null;
  onPick: (id: string) => void;
  onShow: (id: string) => void;
  shown: string | null;
}) {
  return (
    <Stack gap={2}>
      {ids.map((id, n) => {
        const motion = motions.get(id);
        if (!motion) return <Text key={id} small muted>Deleted</Text>;
        const done = motion.status === "done";
        return (
          <Row key={id} justify="between">
            <span>
              Version {n + 1} <Text small muted mono>{motion.name}</Text>{" "}
              {done ? <Badge tone="success">{motion.clip?.duration?.toFixed(1)} s</Badge>
                : motion.status === "failed" ? <Badge tone="danger">Failed</Badge> : <Badge tone="info" pulse>{motion.step}</Badge>}
            </span>
            <Row>
              <Button size="sm" variant="ghost" disabled={!done || shown === id} onClick={() => onShow(id)}>On the dummy</Button>
              <Button size="sm" variant={picked === id ? "primary" : "secondary"} disabled={!done} onClick={() => onPick(id)}>
                {picked === id ? "Picked" : "Pick"}
              </Button>
            </Row>
          </Row>
        );
      })}
    </Stack>
  );
}

function SkillSlot({ record, index, assets, motions, hero, onPicks }: {
  record: CharacterSet;
  index: number;
  assets: Map<string, Asset>;
  motions: Map<string, Motion>;
  hero: Asset | undefined;
  onPicks: (picks: SetPicksInput) => void;
}) {
  const slot = record.slots.skills[index];
  const pick = record.picks.skills[index];
  const [release, setRelease] = useState(pick.release);
  const [dummy, setDummy] = useState<string | null>(null);
  const setSkill = (change: SetPicksInput["skills"] extends (infer T)[] | undefined ? T : never) =>
    onPicks({ skills: record.picks.skills.map((p, n) => (n === index ? { ...p, ...change } : p)) });
  const motion = pick.motion ? motions.get(pick.motion) : undefined;
  const effect = pick.effect ? assets.get(pick.effect) : undefined;
  const shownMotion = dummy ? motions.get(dummy) : undefined;
  return (
    <Card title={slot.name} subtitle={slot.description}>
      <Grid cols={2} min={320}>
        <Stack gap={4}>
          {slot.effects.length > 0 && (
            <Stack gap={2}>
              <Text small muted>Effect (hover to play)</Text>
              <AssetOptions ids={slot.effects} assets={assets} picked={pick.effect} frames
                            onPick={(id) => setSkill({ effect: id })} />
              {effect && (
                <Stack gap={2}>
                  <Text small muted>Make a part of the picked effect again (it goes back to gate 2, then on by autopilot):</Text>
                  <PartRedos asset={effect} />
                </Stack>
              )}
            </Stack>
          )}
          {slot.motions.length > 0 && (
            <Stack gap={2}>
              <Text small muted>Animation</Text>
              <MotionOptions ids={slot.motions} motions={motions} picked={pick.motion} shown={dummy}
                             onPick={(id) => { setDummy(null); setSkill({ motion: id }); }} onShow={setDummy} />
              {motion && <MotionVersions motion={motion} />}
            </Stack>
          )}
          <Field label={`Release: ${Math.round(release * 100)}% of the animation`} hint="When the effect leaves the hand.">
            {(id) => <Input id={id} type="range" min={0} max={1} step={0.01} value={release}
                            onChange={(e) => setRelease(Number(e.target.value))}
                            onPointerUp={() => setSkill({ release })} onKeyUp={() => setSkill({ release })} />}
          </Field>
        </Stack>
        {shownMotion?.url ? (
          <Stack gap={2}>
            <ModelViewer url={localUrl(shownMotion.url)} clip={shownMotion.name} />
            <Button size="sm" variant="ghost" onClick={() => setDummy(null)}>Back to the hero</Button>
          </Stack>
        ) : !hero ? <StageMessage>Pick a rigged hero to try the skill on it.</StageMessage>
          : !motion && !effect ? <StageMessage>Pick an animation or an effect.</StageMessage>
          : <SkillTry hero={hero} clip={motion?.name ?? "cast"} effect={effect} release={release}
                      motions={[...motions.values()]} />}
      </Grid>
    </Card>
  );
}

export function SetPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const set = useSet(id);
  const assetList = useAssets({});
  const motionList = useMotions();
  const [deleting, setDeleting] = useState(false);
  const pick = useApiMutation((picks: SetPicksInput) => api.pickSet(id, picks), [keys.set(id), keys.sets]);
  const build = useApiMutation(() => api.buildSet(id), [keys.set(id), keys.sets, keys.assets]);
  const remove = useApiMutation(() => api.deleteSet(id), [keys.sets]);
  if (set.isLoading || assetList.isLoading || motionList.isLoading) return <Loading />;
  if (set.error || !set.data) return <ErrorNotice error={set.error ?? "Not found"} />;
  const record = set.data;
  const assets = new Map((assetList.data ?? []).map((a) => [a.id, a]));
  const motions = new Map((motionList.data ?? []).map((m) => [m.id, m]));
  const hero = record.picks.hero ? assets.get(record.picks.hero) : undefined;
  const status = SET_STATUS[record.status];
  const members = [...record.slots.hero, ...record.slots.items.flatMap((s) => s.options), ...record.slots.skills.flatMap((s) => s.effects)];
  const making = members.filter((m) => { const a = assets.get(m); return a && (a.status === "queued" || a.status === "running"); }).length
    + record.slots.skills.flatMap((s) => s.motions).filter((m) => { const x = motions.get(m); return x && x.status !== "done" && x.status !== "failed"; }).length;
  const onPicks = (picks: SetPicksInput) => pick.mutate(picks);
  return (
    <Stack gap={5}>
      <PageHeader title={record.name}
                  subtitle={<>{record.variations} versions of each · <Badge tone={status.tone}>{status.label}</Badge>
                    {making > 0 && <> · {making} still being made</>}</>}
                  actions={
                    <Row>
                      <Button variant="ghost" onClick={() => setDeleting(true)}>Delete</Button>
                      <Button variant="primary" busy={build.isPending || record.status === "building"}
                              disabled={record.status === "building"} onClick={() => build.mutate(undefined)}>
                        {record.status === "built" ? "Build again" : "Build the set"}
                      </Button>
                    </Row>
                  } />
      {(pick.error || build.error) && <ErrorNotice error={pick.error ?? build.error} />}
      {record.status === "building" && <Notice title="Building">{record.build?.step}</Notice>}
      {record.status === "failed" && <Notice tone="danger" title="The build failed">{record.build?.error}</Notice>}
      {record.status === "built" && (
        <Notice tone="success" title="Built">
          {record.installed_to ? <>The manifest is in the game project: <code>{record.installed_to}</code></> : "The manifest is ready."}
        </Notice>
      )}
      <Card title="Hero" subtitle="Click a version at gate 3 (or done) to pick it.">
        <AssetOptions ids={record.slots.hero} assets={assets} picked={record.picks.hero} onPick={(h) => onPicks({ hero: h })} />
      </Card>
      {record.slots.items.map((slot, n) => (
        <Card key={n} title={slot.label} subtitle={slot.kind === "weapon" ? "Weapon" : "Gear"}>
          <AssetOptions ids={slot.options} assets={assets} picked={record.picks.items[n]}
                        onPick={(item) => onPicks({ items: record.picks.items.map((p, k) => (k === n ? item : p)) })} />
        </Card>
      ))}
      {record.slots.skills.map((_, n) => (
        <SkillSlot key={n} record={record} index={n} assets={assets} motions={motions} hero={hero} onPicks={onPicks} />
      ))}
      {record.manifest && (
        <Card title="Manifest" subtitle="What the game loads: the hero, its items and each skill's animation, release time and effect.">
          <CodeBlock code={JSON.stringify(record.manifest, null, 2)} />
        </Card>
      )}
      <ConfirmDialog open={deleting} title="Delete this character set?" confirmLabel="Delete" busy={remove.isPending}
                     error={remove.error} body="The assets and skill animations it made stay; only the set and its picks go."
                     onClose={() => setDeleting(false)}
                     onConfirm={() => remove.mutate(undefined, { onSuccess: () => navigate("/sets") })} />
    </Stack>
  );
}
