// Gates 2 and 3 of a skill effect: its parts as flipbooks, then the whole effect as the game plays it.

import { assetFileUrl, type Asset } from "../api/client";
import { label } from "../labels";
import { Badge, Card, Grid, Notice, Row, Stack, Text } from "../ui/kit";
import { EffectSequence } from "../viewers/EffectSequence";
import { SpritePlayer } from "../viewers/SpritePlayer";
import { ChecksCard } from "./Checks";
import { GateBar } from "./Gates";

/** The parts of an effect with their frame URLs. */
function partsOf(asset: Asset) {
  return (asset.outputs.mesh?.parts ?? []).map((p) => ({ ...p, frames: p.frames.map((f) => assetFileUrl(asset.id, f)) }));
}

/** What the language model planned: the kind, the colours and each part's prompts. */
export function EffectPlanCard({ asset }: { asset: Asset }) {
  const plan = asset.outputs.prompt?.plan;
  if (!plan) return null;
  return (
    <Card title="Effect plan" subtitle={`${label(plan.kind)} · the candidates show the ${plan.main}`}>
      <Stack gap={3}>
        <Row>
          {plan.palette.map((c) => (
            <Badge key={c}>
              <span className="swatch" style={{ background: c }} /> {c}
            </Badge>
          ))}
        </Row>
        {plan.parts.map((p) => (
          <Stack key={p.part} gap={1}>
            <Row>
              <Text small><strong>{label(p.part)}</strong></Text>
              <Badge>{p.loop ? "loops" : "once"}</Badge>
              <Badge>{p.blend === "add" ? "glows (additive)" : "solid"}</Badge>
              <Text small faint>×{p.scale}</Text>
            </Row>
            <Text small muted wrap>{p.picture}</Text>
            <Text small faint wrap>Moves: {p.motion}</Text>
          </Stack>
        ))}
      </Stack>
    </Card>
  );
}

export function EffectGate2({ asset, readOnly = false }: { asset: Asset; readOnly?: boolean }) {
  const mesh = asset.outputs.mesh;
  const parts = partsOf(asset);
  return (
    <Stack gap={4}>
      <Card title="Flipbooks" subtitle="Each part drawn in the picked look and animated by Wan 2.2; black became see-through.">
        <Grid cols={Math.min(3, Math.max(1, parts.length))}>
          {parts.map((p) => (
            <Stack key={p.part} gap={2}>
              <SpritePlayer frames={p.frames} fps={mesh?.fps ?? 16} />
              <Row>
                <Text small><strong>{label(p.part)}</strong></Text>
                <Badge>{p.loop ? "loops" : "once"}</Badge>
                <Text small faint>{p.frames.length} frames</Text>
              </Row>
            </Stack>
          ))}
        </Grid>
      </Card>
      <ChecksCard checks={mesh?.checks} />
      {!readOnly && (
        <GateBar gate={2} asset={asset} approveLabel="Approve and put it together" />
      )}
    </Stack>
  );
}

export function EffectGate3({ asset, readOnly = false }: { asset: Asset; readOnly?: boolean }) {
  const sequence = asset.outputs.rig?.sequence;
  const parts = partsOf(asset);
  if (!sequence) return <Notice tone="warning">This effect has no sequence yet.</Notice>;
  return (
    <Stack gap={4}>
      <Card title="The effect in play"
            subtitle={sequence.kind === "projectile" ? "Cast, flight with its trail, impact: what play(from, to) does in Godot."
              : sequence.kind === "burst" ? "The impact where the skill lands." : "The aura, looping on its character."}>
        <EffectSequence kind={sequence.kind} parts={parts} fps={asset.outputs.mesh?.fps ?? 16} palette={sequence.palette}
                        travelSpeed={sequence.travel_speed} particles={sequence.particles} />
      </Card>
      <ChecksCard checks={asset.outputs.rig?.checks} />
      {!readOnly && <GateBar gate={3} asset={asset} approveLabel="Approve and export" />}
    </Stack>
  );
}
