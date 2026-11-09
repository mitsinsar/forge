import { useState } from "react";
import { useAssets, useMotions } from "../api/queries";
import { SkillControls, SkillTry, hasSequence, isRiggedHero } from "../components/SkillTry";
import { Card, EmptyState, Field, Grid, Loading, PageHeader, Select, Stack } from "../ui/kit";

/** Any rigged hero with any animation and any effect, to see how a skill looks before a set is built. */
export function SkillTestPage() {
  const heroes = useAssets({ kind: "hero" });
  const effects = useAssets({ kind: "effect" });
  const motions = useMotions();
  const [heroId, setHeroId] = useState("");
  const [clip, setClip] = useState("");
  const [effectId, setEffectId] = useState("");
  const [release, setRelease] = useState(0.45);
  if (heroes.isLoading || effects.isLoading || motions.isLoading) return <Loading />;
  const rigged = (heroes.data ?? []).filter(isRiggedHero);
  const ready = (effects.data ?? []).filter(hasSequence);
  const hero = rigged.find((h) => h.id === heroId) ?? rigged[0];
  const effect = ready.find((e) => e.id === effectId);
  return (
    <Stack gap={5}>
      <PageHeader title="Skill test" subtitle="A hero, an animation and an effect together, as the skill will look." />
      {!rigged.length ? (
        <EmptyState title="No rigged heroes yet" body="Make a biped hero and take it to gate 3 first." />
      ) : (
        <Grid cols={2} min={320}>
          <Card title="Skill">
            <Stack gap={4}>
              <Field label="Hero">
                {(id) => <Select id={id} value={hero?.id ?? ""} onChange={(e) => { setHeroId(e.target.value); }}
                                 options={rigged.map((h) => ({ value: h.id, label: h.name }))} />}
              </Field>
              <SkillControls hero={hero} clip={clip} onClip={setClip} effects={ready} effectId={effectId}
                             onEffect={setEffectId} release={release} onRelease={setRelease} motions={motions.data ?? []} />
            </Stack>
          </Card>
          <Card title={hero?.name ?? "Hero"} subtitle={clip || "Pick an animation"}>
            <SkillTry hero={hero} clip={clip} effect={effect} release={release} motions={motions.data ?? []} />
          </Card>
        </Grid>
      )}
    </Stack>
  );
}
