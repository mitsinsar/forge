// The three approval gates of an asset, plus its done and failed views.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, assetFileUrl, localUrl, type Asset, type Gate, type Grip, type RejectInput, type Socket } from "../api/client";
import { keys, useApiMutation, useArchetypes } from "../api/queries";
import { isCharacter, label, MESH_PRESETS, meshPreset } from "../labels";
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
  MediaCard,
  Notice,
  Row,
  SectionTitle,
  Select,
  Stack,
  Tabs,
  Text,
  TextArea,
  Thumb,
} from "../ui/kit";
import { SpritePlayer } from "../viewers/SpritePlayer";
import { ModelViewer } from "./Viewer";
import { CheckBadge, ChecksCard } from "./Checks";
import { JointGuide } from "./JointGuide";
import type { Vec3 } from "../viewers/ModelViewer";

const isWeaponKind = (kind: string) => kind === "weapon";


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
  canReject = true,
}: {
  gate: Gate;
  asset: Asset;
  approveLabel: string;
  canApprove?: boolean;
  /** False hides Reject (gate 1 of the user's own image: there is nothing to redo). */
  canReject?: boolean;
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
        {canReject && (
          <Button variant="danger" onClick={() => setRejecting(true)}>
            Reject…
          </Button>
        )}
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

/** ``readOnly`` shows a finished gate again (from the step bar): the pick, no actions. */
export function Gate1({ asset, readOnly = false }: { asset: Asset; readOnly?: boolean }) {
  const prompt = asset.outputs.prompt;
  const upload = prompt?.source === "upload";
  const [pickedByUser, setPicked] = useState<number | null>(null);
  // The user's own image is the only candidate; derived, so it holds even when the outputs load after mounting.
  const picked = readOnly ? asset.chosen_candidate : upload ? 1 : pickedByUser;
  const [archetype, setArchetype] = useState(asset.archetype ?? "");
  const archetypes = useArchetypes();
  const rerun = useApiMutation((value: string) => api.reject(asset.id, 1, { archetype: value }), [keys.assets, keys.asset(asset.id)]);
  const candidates = asset.urls?.candidates ?? [];
  const checksOf = (n: number) => prompt?.candidate_checks?.find((c) => c.candidate === n)?.checks;
  const touching = prompt?.candidate_checks
    ? []
    : (prompt?.pose_check ?? []).filter((c) => c.arms_clear === false).map((c) => c.candidate);

  return (
    <Stack gap={4}>
      <Card
        title={readOnly ? "Preview · approved" : upload ? "Gate 1 · Check your image" : "Gate 1 · Pick a preview"}
        subtitle={
          readOnly
            ? upload
              ? "Your own image; the mesh was built from it."
              : `Attempt ${prompt?.attempt ?? 1}. The highlighted image is the one the mesh was built from.`
            : upload
              ? "The mesh is built from this image. Check the archetype below, then build the mesh."
              : `Attempt ${prompt?.attempt ?? 1}. Pick the image the mesh is built from.`
        }
      >
        <Stack gap={4}>
          {!readOnly && touching.length > 0 && (
            <Notice tone="warning" title={touching.length === 1 ? `Candidate ${touching[0]}: arms touch the body` : `Candidates ${touching.join(", ")}: arms touch the body`}>
              Where the hands touch the body or legs, the 3D model fuses them into one piece, and they
              stretch when the character moves. Pick a candidate without this warning, or reject to draw new ones.
            </Notice>
          )}
          {!readOnly && !!prompt?.redrawn && (
            <Text small muted>
              {prompt.redrawn} candidate{prompt.redrawn > 1 ? "s were" : " was"} redrawn because the checks failed
              (a held item, several figures, cut off, or arms against the body).
            </Text>
          )}
          <Grid min={180}>
            {candidates.map((url, i) => (
              <Thumb
                key={url}
                src={`/api/assets/${asset.id}/thumb?candidate=${i + 1}&size=512`}
                alt={`Candidate ${i + 1}`}
                selected={picked === i + 1}
                onClick={readOnly ? undefined : () => setPicked(i + 1)}
                label={
                  <Row gap={1}>
                    <Badge>{i + 1}</Badge>
                    {touching.includes(i + 1) && <Badge tone="warning">Arms touch body</Badge>}
                    {!readOnly && <CheckBadge checks={checksOf(i + 1)} />}
                  </Row>
                }
              />
            ))}
          </Grid>
          {!readOnly && (
          <GateBar
            gate={1}
            asset={asset}
            approveLabel={upload ? "Build the mesh" : picked ? `Use candidate ${picked}` : "Pick a candidate"}
            canApprove={picked !== null}
            candidate={picked ?? undefined}
            canReject={!upload}
          />
          )}
        </Stack>
      </Card>

      <Grid cols={2}>
        {upload ? (
          <Card title="Your image" subtitle="No image model ran for this asset.">
            <Stack gap={3}>
              {prompt?.expansion.flags?.map((flag) => (
                <Notice key={flag} tone="warning">
                  {flag}
                </Notice>
              ))}
              <Text muted small>
                Hunyuan3D cuts the background away before it builds the mesh. To try a different image, make a new asset.
              </Text>
            </Stack>
          </Card>
        ) : (
          <Card title="Expanded prompt" subtitle={`Base model: ${label(prompt?.base_model)}`}>
            <Stack gap={3}>
              {prompt?.expansion.flags?.map((flag) => (
                <Notice key={flag} tone="warning">
                  {flag}
                </Notice>
              ))}
              {prompt?.log?.map((line) => (
                <Notice key={line}>{line}</Notice>
              ))}
              <Text wrap>{prompt?.prompt}</Text>
              <SectionTitle>Negative</SectionTitle>
              <Text muted small wrap>
                {prompt?.negative_prompt}
              </Text>
            </Stack>
          </Card>
        )}
        <Card title="Archetype" subtitle="The skeleton and clip set the asset is rigged with.">
          <Stack gap={3}>
            <Row>
              <Badge tone="accent">{label(asset.archetype)}</Badge>
              {asset.archetype_override && <Text small faint>chosen by you</Text>}
            </Row>
            {readOnly ? null : isCharacter(asset.kind) ? (
              <>
                <Field
                  label="Override"
                  hint={
                    upload
                      ? "Pick the body plan your image shows; it decides the skeleton and clips."
                      : "Changing it renders new previews with that archetype's rest pose."
                  }
                >
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
                    {upload
                      ? archetype && archetype !== asset.archetype ? `Use ${label(archetype)}` : "Use this archetype"
                      : archetype && archetype !== asset.archetype ? `Rerun preview as ${label(archetype)}` : "Rerun preview"}
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

/** "Triangle budget 8,000 · High (20 steps, 512) · 37 floating pieces removed" */
function meshSummary(mesh: NonNullable<Asset["outputs"]["mesh"]>): string {
  const parts = [`Triangle budget ${mesh.triangle_budget.toLocaleString()}`];
  const q = mesh.quality;
  if (q?.steps && q.resolution) {
    const preset = MESH_PRESETS.find((p) => p.value === meshPreset(q.steps!, q.resolution!));
    parts.push(`${preset ? `${preset.label} ` : ""}(${q.steps} steps, resolution ${q.resolution})`);
  }
  const floaters = mesh.removed?.floater_pieces;
  if (floaters) parts.push(`${floaters} floating piece${floaters === 1 ? "" : "s"} removed`);
  return parts.join(" · ");
}

export function Gate2({ asset, readOnly = false }: { asset: Asset; readOnly?: boolean }) {
  const mesh = asset.outputs.mesh;
  const [lod, setLod] = useState(0);
  const [wireframe, setWireframe] = useState(false);
  const [showImage, setShowImage] = useState(false);
  const url = localUrl(asset.urls?.mesh);
  const image = localUrl(asset.urls?.chosen_image);
  return (
    <Stack gap={4}>
    <Card
      title={readOnly ? "Mesh · approved" : "Gate 2 · Approve the mesh"}
      subtitle={mesh ? meshSummary(mesh) : undefined}
      actions={
        image && (
          <>
            <Box width={56}>
              <Thumb src={image} alt="Picked preview (click to enlarge)" onClick={() => setShowImage(true)} />
            </Box>
            <Dialog
              open={showImage}
              wide
              title="The image the mesh was built from"
              onClose={() => setShowImage(false)}
              actions={<Button onClick={() => setShowImage(false)}>Close</Button>}
            >
              <Thumb src={image} alt="Picked preview" />
            </Dialog>
          </>
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
        {!!mesh?.views?.length && (
          <Grid min={110}>
            {mesh.views.map((v) => (
              <MediaCard key={v} image={assetFileUrl(asset.id, v)} title={label(v.split("/").pop()!.replace(".png", ""))} />
            ))}
          </Grid>
        )}
        <Text small faint>
          Drag to orbit, scroll to zoom.
          {mesh && ` LODs at ${mesh.lods.map((l) => `${Math.round(l.ratio * 100)}%`).join(" / ")}.`}
          {mesh?.delight !== undefined && (mesh.delight ? " Baked lighting evened out." : " Baked lighting kept as made.")}
        </Text>
        {!readOnly && isCharacter(asset.kind) && <HeightRow asset={asset} />}
        {!readOnly && <GateBar gate={2} asset={asset} approveLabel="Approve mesh" />}
      </Stack>
    </Card>
    {!readOnly && <ChecksCard checks={mesh?.checks} subtitle="Loose pieces and holes in the cleaned mesh." />}
    </Stack>
  );
}

// ---- gate 3: rig and animation ------------------------------------------------

/** Gate 2: rescale a character to another height (reruns only the clean-up, not Hunyuan3D). */
function HeightRow({ asset }: { asset: Asset }) {
  const standard = asset.kind === "boss" ? 2.7 : asset.kind === "monster" ? 1.5 : 1.8;
  const [height, setHeight] = useState(String(asset.height ?? standard));
  const rescale = useApiMutation((value: number) => api.reject(asset.id, 2, { height: value }), [
    keys.assets,
    keys.asset(asset.id),
  ]);
  const value = Number(height);
  const changed = value > 0 && value !== (asset.height ?? standard);
  return (
    <Stack gap={2}>
      <Row>
        <Box width={160}>
          <Field label="Height (m)" hint="Feet to the top of the head.">
            {(id) => (
              <Input id={id} type="number" min={0.3} max={10} step={0.05} value={height}
                     onChange={(e) => setHeight(e.target.value)} />
            )}
          </Field>
        </Box>
        <Button disabled={!changed} busy={rescale.isPending} onClick={() => rescale.mutate(value)}>
          Rescale
        </Button>
      </Row>
      {rescale.error && <ErrorNotice error={rescale.error} />}
    </Stack>
  );
}

/** Gate 3, gear and weapons: the hero they are fitted on (or the base mannequin). */
function WearerCard({ asset }: { asset: Asset }) {
  const rig = asset.outputs.rig;
  const wearers = useQuery({ queryKey: ["wearers", asset.project_id], queryFn: () => api.wearers(asset.project_id) });
  const current = rig?.wearer?.id ?? "";
  const [choice, setChoice] = useState(current);
  const refit = useApiMutation((wearer: string) => api.reject(asset.id, 3, { wearer }), [
    keys.assets,
    keys.asset(asset.id),
  ]);
  return (
    <Card
      title="Wearer"
      subtitle="The body it is fitted on. Fit it on the hero who will wear it, so it matches that hero's size and build."
      actions={<Badge>{rig?.wearer?.name ?? "Test dummy"}</Badge>}
    >
      <Stack gap={3}>
        <Row>
          <Box minWidth={240}>
            <Select
              aria-label="Wearer"
              name="wearer"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
              options={[
                { value: "", label: "Test dummy (base mannequin, 1.8 m)" },
                ...(wearers.data ?? []).map((w) => ({ value: w.id, label: w.height ? `${w.name} (${w.height} m)` : w.name })),
              ]}
            />
          </Box>
          <Button variant="primary" disabled={choice === current} busy={refit.isPending} onClick={() => refit.mutate(choice)}>
            Refit on this wearer
          </Button>
        </Row>
        {wearers.data && wearers.data.length === 0 && (
          <Text small muted>No rigged biped heroes in this project yet. Heroes rigged before this version need a re-rig to be listed.</Text>
        )}
        {refit.error && <ErrorNotice error={refit.error} />}
      </Stack>
    </Card>
  );
}

/** The grip fields the user edits at gate 3. */
type GripEdit = Required<Pick<Grip, "socket" | "position" | "rotation">>;

function GripFields({ grip, onChange }: { grip: GripEdit; onChange: (grip: GripEdit) => void }) {
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

export function Gate3({ asset, godot, readOnly = false }: { asset: Asset; godot: boolean; readOnly?: boolean }) {
  const rig = asset.outputs.rig;
  const [clips, setClips] = useState<string[]>([]);
  const [clipChoice, setClip] = useState<string | null>(null);
  const sprites = rig?.sprites;
  const directions = sprites ? [...sprites.directions, ...Object.keys(sprites.mirrored)] : [];
  const [direction, setDirection] = useState<string>(directions[0] ?? "");
  const round = (v: [number, number, number]) => v.map((n) => Math.round(n * 10000) / 10000) as [number, number, number];
  const startGrip: GripEdit = {
    socket: rig?.grip?.socket ?? asset.grip?.socket ?? "weapon_r",
    position: round(rig?.grip?.position ?? asset.grip?.position ?? [0, 0, 0]),
    rotation: round(rig?.grip?.rotation ?? asset.grip?.rotation ?? [0, 0, 0]),
  };
  const [grip, setGrip] = useState(startGrip);
  const gripChanged = JSON.stringify(grip) !== JSON.stringify(startGrip);

  // Joint editor (characters): joints dragged in the viewer, sent with a re-rig.
  const [editing, setEditing] = useState(false);
  const [moved, setMoved] = useState<Record<string, Vec3>>({});
  const [mirror, setMirror] = useState(true);
  const [pickedJoint, setPickedJoint] = useState<string>();
  const [twisted, setTwisted] = useState<Record<string, number>>({}); // degrees, on top of the rig's
  const twistedCount = Object.keys(twisted).length;
  const twistJoint = (name: string, degrees: number) =>
    setTwisted((current) => {
      const clamp = (v: number) => Math.max(-180, Math.min(180, Math.round(v)));
      const next = { ...current, [name]: clamp(degrees) };
      const side = name.match(/_(l|r)$/)?.[1];
      const twin = side && name.slice(0, -1) + (side === "l" ? "r" : "l");
      // A mirror image turns the other way: both knees turn out (or in) together.
      if (mirror && twin && rig?.joints?.[twin]) next[twin] = clamp(-degrees);
      return next;
    });
  const shownTwists = useMemo(() => {
    // The facing lines show the rig's result, so only the twists not yet re-rigged turn them.
    const done = rig?.joint_twists ?? {};
    return Object.fromEntries(Object.entries(twisted).map(([k, v]) => [k, v - (done[k] ?? 0)]));
  }, [twisted, rig?.joint_twists]);
  const [jointWireframe, setJointWireframe] = useState(false);
  const movedCount = Object.keys(moved).length;
  const rerig = useApiMutation(
    // Only what changed is sent: an empty set of joints means "back to the detected joints".
    (input: { joints?: Record<string, Vec3>; twists?: Record<string, number> }) =>
      api.reject(asset.id, 3, {
        ...(input.joints ? { joints: input.joints } : {}),
        ...(input.twists && Object.keys(input.twists).length ? { twists: input.twists } : {}),
      }),
    [
    keys.assets,
    keys.asset(asset.id),
  ]);
  const moveJoint = (name: string, head: Vec3) =>
    setMoved((current) => {
      const next = { ...current, [name]: head };
      const side = name.match(/_(l|r)$/)?.[1];
      const twin = side && name.slice(0, -1) + (side === "l" ? "r" : "l");
      if (mirror && twin && rig?.joints?.[twin]) next[twin] = [-head[0], head[1], head[2]];
      return next;
    });
  const canEditJoints = !readOnly && !!rig?.joints && asset.kind !== "gear" && !isWeaponKind(asset.kind);

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
        title={readOnly ? "Animation · approved" : "Gate 3 · Approve the animated result"}
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
              {url && (
                <ModelViewer
                  url={url}
                  clip={clip}
                  onClips={setClips}
                  joints={rig?.joints}
                  moved={moved}
                  editJoints={editing}
                  twists={shownTwists}
                  onJointMove={moveJoint}
                  onJointPick={setPickedJoint}
                  wireframe={editing && jointWireframe}
                />
              )}
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
          {!readOnly && (
          <GateBar
            gate={3}
            asset={asset}
            approveLabel="Approve and export"
            rejectExtra={
              isWeapon && gripChanged ? <Text small muted>The new grip is sent with the rejection.</Text> : undefined
            }
            rejectInput={() => (isWeapon && gripChanged ? { grip } : {})}
          />
          )}
        </Stack>
      </Card>
      {!readOnly && <ChecksCard checks={rig?.checks} subtitle="How well the skeleton fits, and how much each clip stretches the mesh." />}
      {canEditJoints && (
        <Card
          title="Joints"
          subtitle="If an elbow, knee or hip bends in the wrong place, move that joint and re-rig."
          actions={
            movedCount > 0 ? (
              <Badge tone="accent">{movedCount} moved</Badge>
            ) : rig?.joint_overrides?.length ? (
              <Badge>{rig.joint_overrides.length} set by you</Badge>
            ) : undefined
          }
        >
          <Stack gap={3}>
            <Row>
              <Button variant={editing ? "primary" : "secondary"} onClick={() => setEditing(!editing)}>
                {editing ? "Stop editing" : "Adjust joints"}
              </Button>
              {editing && (
                <Checkbox label="Move left and right together" checked={mirror} onChange={setMirror} />
              )}
              {editing && <Checkbox label="Wireframe" checked={jointWireframe} onChange={setJointWireframe} />}
            </Row>
            {editing && (
              <Text small muted>
                The model holds its rest pose. Click a joint, then drag the arrows. Orange joints are moved. Use
                Front, Left and Flat above the model to line joints up straight on.
              </Text>
            )}
            {editing && pickedJoint && rig?.joints?.[pickedJoint]?.axis && (
              <Row>
                <Text small>Twist {pickedJoint.replace(/_/g, " ")}</Text>
                <Button size="sm" onClick={() => twistJoint(pickedJoint, (twisted[pickedJoint] ?? rig?.joint_twists?.[pickedJoint] ?? 0) - 5)}>
                  ⟲ 5°
                </Button>
                <Box width={90}>
                  <Input
                    aria-label="Twist in degrees"
                    type="number"
                    step={5}
                    value={twisted[pickedJoint] ?? rig?.joint_twists?.[pickedJoint] ?? 0}
                    onChange={(e) => twistJoint(pickedJoint, Number(e.target.value) || 0)}
                  />
                </Box>
                <Button size="sm" onClick={() => twistJoint(pickedJoint, (twisted[pickedJoint] ?? rig?.joint_twists?.[pickedJoint] ?? 0) + 5)}>
                  ⟳ 5°
                </Button>
                <Text small muted>The green line shows which way the joint faces: a knee's should point forward.</Text>
              </Row>
            )}
            {editing && (
              <JointGuide
                picked={pickedJoint}
                // The figure names a joint without its side: take the left one (mirroring moves the right).
                onPick={(base) => setPickedJoint(rig?.joints?.[base] || !rig?.joints?.[`${base}_l`] ? base : `${base}_l`)}
              />
            )}
            <Row>
              <Button
                variant="primary"
                disabled={movedCount === 0 && twistedCount === 0}
                busy={rerig.isPending}
                onClick={() => rerig.mutate({ ...(movedCount ? { joints: moved } : {}), twists: twisted }, { onSuccess: () => { setMoved({}); setTwisted({}); } })}
              >
                Re-rig with these joints
              </Button>
              <Button variant="ghost" disabled={movedCount === 0 && twistedCount === 0}
                      onClick={() => { setMoved({}); setTwisted({}); }}>
                Undo moves
              </Button>
              {(!!rig?.joint_overrides?.length || !!Object.keys(rig?.joint_twists ?? {}).length) && (
                <Button variant="ghost" busy={rerig.isPending} onClick={() => rerig.mutate({ joints: {} })}>
                  Back to the detected joints
                </Button>
              )}
            </Row>
            {rerig.error && <ErrorNotice error={rerig.error} />}
          </Stack>
        </Card>
      )}
      {isWeapon && !readOnly && (
        <Card
          title="Grip"
          subtitle="Where the weapon sits in the hand. Change it and reject to re-rig with the new grip."
          actions={
            gripChanged ? (
              <Badge tone="accent">Changed</Badge>
            ) : rig?.grip?.source === "manual" ? (
              <Badge>Set by you</Badge>
            ) : rig?.grip?.source === "auto" ? (
              <Badge tone="success">Found on the handle</Badge>
            ) : undefined
          }
        >
          <Stack gap={3}>
            <GripFields grip={grip} onChange={setGrip} />
            {rig?.grip?.second && (
              <Text small muted>
                Two-handed: the other hand holds it on {rig.grip.second.socket}, 12 cm towards the blade.
              </Text>
            )}
          </Stack>
        </Card>
      )}
      {!readOnly && rig && (asset.kind === "gear" || isWeapon) && <WearerCard asset={asset} />}
      {rig?.gear && !readOnly && (
        <Card title="Fit on the base body" subtitle={`Shown on the mannequin playing ${rig.preview_clips.join(" and ")}.`}>
          <DefList
            items={[
              ["Slot", label(rig.gear.slot)],
              ["Body faces hidden under it", rig.gear.masked_faces],
              ["Points pushed out of the body", rig.gear.inside_before],
            ]}
          />
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
