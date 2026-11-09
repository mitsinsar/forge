// Trying a skill on a hero: the hero plays an animation (its own clip, or a generated skill
// animation made onto it on request) with an effect leaving its hand at the release time.

import { heroSkillUrl, localUrl, type Asset, type Motion } from "../api/client";
import { Field, Grid, Input, Select, Stack, StageMessage, Text, type Option } from "../ui/kit";
import { partsOf } from "./EffectGates";
import { SkillStage } from "./Viewer";

export const isRiggedHero = (a: Asset) => a.kind === "hero" && a.archetype === "biped" && !!a.outputs.rig;
export const hasSequence = (a: Asset) => a.kind === "effect" && !!a.outputs.rig?.sequence;

/** The 3D stage for a hero, an animation (a clip name) and an effect. */
export function SkillTry({ hero, clip, effect, release, motions }: {
  hero: Asset | undefined;
  clip: string;
  effect: Asset | undefined | null;
  release: number;
  motions: Motion[];
}) {
  if (!hero) return <StageMessage>Pick a hero.</StageMessage>;
  const own = (hero.outputs.rig?.clips ?? []).some((c) => c.name === clip);
  const generated = motions.find((m) => m.name === clip && m.status === "done");
  const url = !own && generated ? heroSkillUrl(hero.id, clip) : localUrl(hero.urls?.animated);
  if (!url) return <StageMessage>The hero has no animated model.</StageMessage>;
  const sequence = effect?.outputs.rig?.sequence;
  const stageEffect = effect && sequence ? {
    kind: sequence.kind, parts: partsOf(effect), fps: effect.outputs.mesh?.fps ?? 16, palette: sequence.palette,
    travelSpeed: sequence.travel_speed,
  } : null;
  return <SkillStage key={url} url={url} clip={clip || undefined} effect={stageEffect} release={release} />;
}

/** The animation, effect and release time controls. */
export function SkillControls({ hero, clip, onClip, effects, effectId, onEffect, release, onRelease, motions }: {
  hero: Asset | undefined;
  clip: string;
  onClip: (clip: string) => void;
  effects: Asset[];
  effectId: string;
  onEffect: (id: string) => void;
  release: number;
  onRelease: (value: number) => void;
  motions: Motion[];
}) {
  const own: Option[] = (hero?.outputs.rig?.clips ?? []).map((c) => ({ value: c.name, label: c.name }));
  const ownNames = new Set(own.map((o) => o.value));
  const generated: Option[] = motions.filter((m) => m.status === "done" && !ownNames.has(m.name))
    .map((m) => ({ value: m.name, label: `${m.title} (${m.name})` }));
  return (
    <Stack gap={3}>
      <Grid cols={2}>
        <Field label="Animation" hint="Skill animations first (put on the hero in a few seconds), then its own clips.">
          {(id) => <Select id={id} value={clip} onChange={(e) => onClip(e.target.value)}
                           options={[...generated, ...own]} placeholder="Pick an animation" />}
        </Field>
        <Field label="Effect">
          {(id) => <Select id={id} value={effectId} onChange={(e) => onEffect(e.target.value)} placeholder="No effect"
                           options={effects.map((e) => ({ value: e.id, label: e.name }))} />}
        </Field>
      </Grid>
      <Field label={`Release: ${Math.round(release * 100)}% of the animation`}
             hint="When the effect leaves the hand.">
        {(id) => <Input id={id} type="range" min={0} max={1} step={0.01} value={release}
                        onChange={(e) => onRelease(Number(e.target.value))} />}
      </Field>
      <Text small muted>The camera starts at the hero's right side; drag to look around.</Text>
    </Stack>
  );
}
