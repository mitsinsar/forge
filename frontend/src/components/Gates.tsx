// The three approval gates of an asset, plus its done and failed views.

import { lazy, Suspense, useState, type ComponentProps } from "react";
import { api, assetFileUrl, localUrl, type Asset, type Gate, type Grip, type RejectInput, type Socket } from "../api/client";
import { keys, useApiMutation, useArchetypes } from "../api/queries";
import { isCharacter, label } from "../labels";
import {
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  DefList,
  Dialog,
  ErrorNotice,
  ExternalLink,
  Field,
  Grid,
  Input,
  LinkButton,
  Notice,
  Row,
  SectionTitle,
  Select,
  Stack,
  StageMessage,
  Tabs,
  Text,
  TextArea,
  Thumb,
} from "../ui/kit";
import { SpritePlayer } from "../viewers/SpritePlayer";

// three.js is large; load the 3D viewer only when a gate shows it.
const LazyModelViewer = lazy(() => import("../viewers/ModelViewer").then((m) => ({ default: m.ModelViewer })));

function ModelViewer(props: ComponentProps<typeof LazyModelViewer>) {
  return (
    <Suspense fallback={<StageMessage>Loading viewer…</StageMessage>}>
      <LazyModelViewer {...props} />
    </Suspense>
  );
}

// ---- shared: approve / reject bar ---------------------------------------------

function useGateActions(asset: Asset, gate: Gate) {
  const approve = useApiMutation((candidate?: number) => api.approve(asset.id, gate, candidate), [keys.assets, keys.asset(asset.id)]);
  const reject = useApiMutation((input: RejectInput) => api.reject(asset.id, gate, input), [keys.assets, keys.asset(asset.id)]);
  return { approve, reject };
}

function RejectDialog({
  open,
  gate,
  onClose,
  onReject,
  busy,
  error,
  extra,
}: {
  open: boolean;
  gate: Gate;
  onClose: () => void;
  onReject: (note: string) => void;
  busy: boolean;
  error: unknown;
  extra?: React.ReactNode;
}) {
  const [note, setNote] = useState("");
  const what = { 1: "the preview images", 2: "the mesh", 3: "rigging and animation" }[gate];
  return (
    <Dialog
      open={open}
      title={`Reject gate ${gate}`}
      onClose={onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" busy={busy} onClick={() => onReject(note.trim())}>
            Reject and redo
          </Button>
        </>
      }
    >
      <Stack gap={4}>
        <Text muted>Only {what} will be redone; everything approved before stays.</Text>
        <Field label="Note (optional)" hint="Folded into the next attempt.">
          {(id) => (
            <TextArea
              id={id}
              autoFocus
              value={note}
              maxLength={2000}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. less bulky shoulders, a longer cape"
            />
          )}
        </Field>
        {extra}
        <ErrorNotice error={error} />
      </Stack>
    </Dialog>
  );
}

function GateBar({
  gate,
  asset,
  approveLabel,
  canApprove = true,
  candidate,
  rejectExtra,
  rejectInput,
}: {
  gate: Gate;
  asset: Asset;
  approveLabel: string;
  canApprove?: boolean;
  /** Gate 1: the picked candidate sent with the approval. */
  candidate?: number;
  rejectExtra?: React.ReactNode;
  rejectInput?: () => Omit<RejectInput, "note">;
}) {
  const { approve, reject } = useGateActions(asset, gate);
  const [rejecting, setRejecting] = useState(false);
  return (
    <Stack>
      <ErrorNotice error={approve.error} />
      <Row justify="end">
        <Button variant="danger" onClick={() => setRejecting(true)}>
          Reject…
        </Button>
        <Button variant="primary" disabled={!canApprove} busy={approve.isPending} onClick={() => approve.mutate(candidate)}>
          {approveLabel}
        </Button>
      </Row>
      <RejectDialog
        open={rejecting}
        gate={gate}
        busy={reject.isPending}
        error={reject.error}
        extra={rejectExtra}
        onClose={() => setRejecting(false)}
        onReject={(note) =>
          reject.mutate({ note: note || undefined, ...(rejectInput?.() ?? {}) }, { onSuccess: () => setRejecting(false) })
        }
      />
    </Stack>
  );
}

// ---- gate 1: preview ------------------------------------------------------------

export function Gate1({ asset }: { asset: Asset }) {
  const [picked, setPicked] = useState<number | null>(null);
  const [archetype, setArchetype] = useState(asset.archetype ?? "");
  const archetypes = useArchetypes();
  const rerun = useApiMutation((value: string) => api.reject(asset.id, 1, { archetype: value }), [keys.assets, keys.asset(asset.id)]);
  const prompt = asset.outputs.prompt;
  const candidates = asset.urls?.candidates ?? [];

  return (
    <Stack gap={4}>
      <Card
        title="Gate 1 · Pick a preview"
        subtitle={`Attempt ${prompt?.attempt ?? 1}. Pick the image the mesh is built from.`}
      >
        <Stack gap={4}>
          <Grid min={180}>
            {candidates.map((url, i) => (
              <Thumb
                key={url}
                src={localUrl(url)}
                alt={`Candidate ${i + 1}`}
                selected={picked === i + 1}
                onClick={() => setPicked(i + 1)}
                label={<Badge>{i + 1}</Badge>}
              />
            ))}
          </Grid>
          <GateBar
            gate={1}
            asset={asset}
            approveLabel={picked ? `Use candidate ${picked}` : "Pick a candidate"}
            canApprove={picked !== null}
            candidate={picked ?? undefined}
          />
        </Stack>
      </Card>

      <Grid cols={2}>
        <Card title="Expanded prompt" subtitle={`Base model: ${label(prompt?.base_model)}`}>
          <Stack gap={3}>
            {prompt?.expansion.flags?.map((flag) => (
              <Notice key={flag} tone="warning">
                {flag}
              </Notice>
            ))}
            <Text wrap>{prompt?.prompt}</Text>
            <SectionTitle>Negative</SectionTitle>
            <Text muted small wrap>
              {prompt?.negative_prompt}
            </Text>
          </Stack>
        </Card>
        <Card title="Archetype" subtitle="The skeleton and clip set the asset is rigged with.">
          <Stack gap={3}>
            <Row>
              <Badge tone="accent">{label(asset.archetype)}</Badge>
              {asset.archetype_override && <Text small faint>chosen by you</Text>}
            </Row>
            {isCharacter(asset.kind) ? (
              <>
                <Field label="Override" hint="Changing it renders new previews with that archetype's rest pose.">
                  {(id) => (
                    <Select
                      id={id}
                      value={archetype}
                      onChange={(e) => setArchetype(e.target.value)}
                      options={(archetypes.data ?? []).map((a) => ({ value: a.id, label: a.name }))}
                    />
                  )}
                </Field>
                <Row justify="end">
                  <Button
                    disabled={!archetype || archetype === asset.archetype}
                    busy={rerun.isPending}
                    onClick={() => rerun.mutate(archetype)}
                  >
                    {archetype && archetype !== asset.archetype ? `Rerun preview as ${label(archetype)}` : "Rerun preview"}
                  </Button>
                </Row>
                <ErrorNotice error={rerun.error} />
              </>
            ) : (
              <Text muted small>
                Gear and weapons are always previewed on the biped mannequin.
              </Text>
            )}
          </Stack>
        </Card>
      </Grid>
    </Stack>
  );
}

// ---- gate 2: mesh ---------------------------------------------------------------

export function Gate2({ asset }: { asset: Asset }) {
  const mesh = asset.outputs.mesh;
  const [lod, setLod] = useState(0);
  const [wireframe, setWireframe] = useState(false);
  const url = localUrl(asset.urls?.mesh);
  return (
    <Card
      title="Gate 2 · Approve the mesh"
      subtitle={mesh ? `Triangle budget ${mesh.triangle_budget.toLocaleString()}` : undefined}
      actions={
        asset.urls?.chosen_image && (
          <Box width={56}>
            <Thumb src={localUrl(asset.urls.chosen_image)} alt="Picked preview" />
          </Box>
        )
      }
    >
      <Stack gap={4}>
        <Row justify="between">
          <Tabs
            label="Level of detail"
            value={String(lod)}
            onChange={(v) => setLod(Number(v))}
            items={(mesh?.lods ?? []).map((l) => ({
              value: String(l.level),
              label: `LOD${l.level} · ${l.triangles.toLocaleString()} tris`,
            }))}
          />
          <Checkbox checked={wireframe} onChange={setWireframe} label="Wireframe" />
        </Row>
        {url && <ModelViewer url={url} lod={lod} wireframe={wireframe} />}
        <Text small faint>
          Drag to orbit, scroll to zoom.
          {mesh && ` LODs at ${mesh.lods.map((l) => `${Math.round(l.ratio * 100)}%`).join(" / ")}.`}
          {mesh?.delight !== undefined && (mesh.delight ? " Baked lighting evened out." : " Baked lighting kept as made.")}
        </Text>
        <GateBar gate={2} asset={asset} approveLabel="Approve mesh" />
      </Stack>
    </Card>
  );
}

// ---- gate 3: rig and animation ------------------------------------------------

function GripFields({ grip, onChange }: { grip: Required<Grip>; onChange: (grip: Required<Grip>) => void }) {
  const axis = (key: "position" | "rotation", i: number, value: string) => {
    const next = [...grip[key]] as [number, number, number];
    next[i] = Number(value) || 0;
    onChange({ ...grip, [key]: next });
  };
  return (
    <Stack gap={3}>
      <Field label="Socket">
        {(id) => (
          <Select
            id={id}
            value={grip.socket}
            onChange={(e) => onChange({ ...grip, socket: e.target.value as Socket })}
            options={[
              { value: "weapon_r", label: "Right hand (weapon_r)" },
              { value: "weapon_l", label: "Left hand (weapon_l)" },
            ]}
          />
        )}
      </Field>
      {(["position", "rotation"] as const).map((key) => (
        <Field key={key} group label={key === "position" ? "Position (m)" : "Rotation (degrees)"}>
          {() => (
            <Grid cols={3}>
              {["X", "Y", "Z"].map((a, i) => (
                <Input
                  key={a}
                  type="number"
                  step={key === "position" ? 0.01 : 1}
                  aria-label={`${key} ${a}`}
                  name={`grip-${key}-${a}`}
                  value={grip[key][i]}
                  onChange={(e) => axis(key, i, e.target.value)}
                />
              ))}
            </Grid>
          )}
        </Field>
      ))}
    </Stack>
  );
}

export function Gate3({ asset, godot }: { asset: Asset; godot: boolean }) {
  const rig = asset.outputs.rig;
  const [clips, setClips] = useState<string[]>([]);
  const [clipChoice, setClip] = useState<string | null>(null);
  const sprites = rig?.sprites;
  const directions = sprites ? [...sprites.directions, ...Object.keys(sprites.mirrored)] : [];
  const [direction, setDirection] = useState<string>(directions[0] ?? "");
  const round = (v: [number, number, number]) => v.map((n) => Math.round(n * 10000) / 10000) as [number, number, number];
  const startGrip: Required<Grip> = {
    socket: rig?.grip?.socket ?? asset.grip?.socket ?? "weapon_r",
    position: round(rig?.grip?.position ?? asset.grip?.position ?? [0, 0, 0]),
    rotation: round(rig?.grip?.rotation ?? asset.grip?.rotation ?? [0, 0, 0]),
  };
  const [grip, setGrip] = useState(startGrip);
  const gripChanged = JSON.stringify(grip) !== JSON.stringify(startGrip);

  const clip = clipChoice ?? rig?.preview_clips.find((c) => clips.includes(c)) ?? clips[0] ?? "";
  const sourceDirection = sprites?.mirrored[direction] ?? direction;
  const frames = sprites?.files[sourceDirection]?.[clip] ?? [];
  const url = localUrl(asset.urls?.animated);
  const isWeapon = asset.kind === "weapon";

  return (
    <Stack gap={4}>
      {rig?.skeleton.fallback && (
        <Notice tone="warning" title="Template fit fell back">
          The archetype's template skeleton didn't fit this shape well, so the raw predicted rig was mapped to the
          closest archetype. Check the clips closely before approving.
        </Notice>
      )}
      <Card
        title="Gate 3 · Approve the animated result"
        subtitle={rig ? `${rig.rig_type} rig · ${rig.skeleton.joints} joints · fit ${Math.round(rig.skeleton.fit_score * 100)}%` : undefined}
      >
        <Stack gap={4}>
          {clips.length > 0 && (
            <Tabs
              label="Clip"
              value={clip}
              onChange={setClip}
              items={clips.map((c) => ({ value: c, label: label(c) }))}
            />
          )}
          <Grid cols={sprites ? 2 : 1}>
            <Stack gap={2}>
              {sprites && <SectionTitle>3D</SectionTitle>}
              {url && <ModelViewer url={url} clip={clip} onClips={setClips} />}
            </Stack>
            {sprites && (
              <Stack gap={2}>
                <Row justify="between">
                  <SectionTitle>
                    Sprites · {sprites.fps} fps · {sprites.frame_size}px
                  </SectionTitle>
                  <Box minWidth={160}>
                    <Select
                      aria-label="Direction"
                      name="direction"
                      value={direction}
                      onChange={(e) => setDirection(e.target.value)}
                      options={directions.map((d) => ({
                        value: d,
                        label: sprites.mirrored[d] ? `${label(d)} (mirrored)` : label(d),
                      }))}
                    />
                  </Box>
                </Row>
                {frames.length ? (
                  <SpritePlayer
                    frames={frames.map((f) => assetFileUrl(asset.id, f))}
                    fps={sprites.fps}
                    flip={!!sprites.mirrored[direction]}
                    pixelated={asset.settings?.render.look === "pixel"}
                  />
                ) : (
                  <Text muted>No frames for this clip.</Text>
                )}
              </Stack>
            )}
          </Grid>
          {godot && !sprites && (
            <Text small faint>
              Sprite frames are rendered at export for this asset.
            </Text>
          )}
          <GateBar
            gate={3}
            asset={asset}
            approveLabel="Approve and export"
            rejectExtra={
              isWeapon && gripChanged ? <Text small muted>The new grip is sent with the rejection.</Text> : undefined
            }
            rejectInput={() => (isWeapon && gripChanged ? { grip } : {})}
          />
        </Stack>
      </Card>
      {isWeapon && (
        <Card
          title="Grip"
          subtitle="Where the weapon sits in the hand. Change it and reject to re-rig with the new grip."
          actions={gripChanged && <Badge tone="accent">Changed</Badge>}
        >
          <GripFields grip={grip} onChange={setGrip} />
        </Card>
      )}
    </Stack>
  );
}

// ---- done -----------------------------------------------------------------------

export function DoneView({ asset }: { asset: Asset }) {
  const exported = asset.outputs.export;
  const reexport = useApiMutation(() => api.reexport(asset.id), [keys.assets, keys.asset(asset.id)]);
  const training = useApiMutation((value: boolean) => api.setTrainingCandidate(asset.id, value), [keys.assets, keys.asset(asset.id)]);
  const files = exported?.files ?? [];
  const fileUrls = asset.urls?.export_files ?? [];
  const animated = localUrl(asset.urls?.animated);

  return (
    <Stack gap={4}>
      <Notice tone="success" title="Exported">
        {exported?.installed_to ? (
          <>
            Written to <code>{exported.installed_to}</code>
          </>
        ) : (
          <>The project has no folder set, so nothing was copied into it. Download the zip instead.</>
        )}
      </Notice>
      <Grid cols={2}>
        <Card
          title="Export"
          subtitle={exported ? `${label(exported.engine)} · ${exported.project_relative}` : undefined}
          actions={
            asset.urls?.zip && (
              <LinkButton href={localUrl(asset.urls.zip)} download variant="primary">
                Download zip
              </LinkButton>
            )
          }
        >
          <Stack gap={4}>
            <Stack gap={1}>
              <SectionTitle>{files.length} files</SectionTitle>
              {files.map((file, i) => (
                <ExternalLink key={file} href={localUrl(fileUrls[i]) ?? "#"} mono>
                  {file}
                </ExternalLink>
              ))}
            </Stack>
            <ErrorNotice error={reexport.error ?? training.error} />
            <Row justify="between">
              <Checkbox
                checked={asset.training_candidate}
                disabled={training.isPending}
                onChange={(value) => training.mutate(value)}
                label="LoRA training candidate"
              />
              <Button busy={reexport.isPending} onClick={() => reexport.mutate(undefined)}>
                Re-export
              </Button>
            </Row>
          </Stack>
        </Card>
        {animated && (
          <Card title="Final asset">
            <ModelViewer url={animated} />
          </Card>
        )}
      </Grid>
    </Stack>
  );
}

// ---- failed ---------------------------------------------------------------------

export function FailedView({ asset }: { asset: Asset }) {
  const retry = useApiMutation(() => api.retry(asset.id), [keys.assets, keys.asset(asset.id)]);
  return (
    <Card title="This stage failed">
      <Stack gap={4}>
        <Notice tone="danger" title={asset.step || "Error"}>
          {asset.error ?? "Unknown error"}
        </Notice>
        <Text muted small>
          Retrying resumes the stage from the files it already made.
        </Text>
        <ErrorNotice error={retry.error} />
        <Row justify="end">
          <Button variant="primary" busy={retry.isPending} onClick={() => retry.mutate(undefined)}>
            Retry
          </Button>
        </Row>
      </Stack>
    </Card>
  );
}

// ---- details --------------------------------------------------------------------

export function AssetDetails({ asset, projectName, profileName }: { asset: Asset; projectName?: string; profileName?: string }) {
  return (
    <Card title="Details">
      <Stack gap={4}>
        <DefList
          items={[
            ["Description", <Text wrap>{asset.description}</Text>],
            ["Project", projectName ?? "—"],
            ["Style profile", profileName ?? "—"],
            ["Archetype", label(asset.archetype) || "Not decided yet"],
            ...(asset.slot ? [["Slot", label(asset.slot)] as [string, string]] : []),
            ...(asset.weapon_class ? [["Weapon class", label(asset.weapon_class)] as [string, string]] : []),
          ]}
        />
        {asset.notes.length > 0 && (
          <Stack gap={2}>
            <SectionTitle>Rejection notes</SectionTitle>
            {asset.notes.map((n, i) => (
              <Text key={i} small>
                <Badge>Gate {n.gate}</Badge> {n.note}
              </Text>
            ))}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}
