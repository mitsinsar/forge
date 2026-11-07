import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, localUrl, type Asset, type Stage } from "../api/client";
import { keys, useApiMutation, useAsset, useProfiles, useProjects } from "../api/queries";
import { AssetStatus } from "../components/AssetStatus";
import { AssetDetails, DoneView, FailedView, Gate1, Gate2, Gate3, StoppedView } from "../components/Gates";
import { KIND_LABEL, STAGE_LABEL, label } from "../labels";
import {
  Badge,
  Checkbox,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
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
  Stack,
  Stepper,
  Text,
  type StepState,
} from "../ui/kit";

const STAGES: Stage[] = ["prompt", "mesh", "rig", "export"];

/** A stage whose finished output can be shown again from the step bar. */
function viewable(asset: Asset, stage: Stage): boolean {
  const current = STAGES.indexOf(asset.stage);
  const finished = asset.status === "done" || STAGES.indexOf(stage) < current;
  return finished && stage !== "export" && !!asset.outputs[stage];
}

function steps(asset: Asset, viewing: Stage | null, setViewing: (stage: Stage | null) => void) {
  const current = STAGES.indexOf(asset.stage);
  return STAGES.map((stage, i) => {
    let state: StepState = i < current ? "done" : i === current ? "current" : "todo";
    let sub: string | undefined;
    if (asset.status === "done") state = "done";
    else if (i === current) {
      if (asset.status === "failed") {
        state = "failed";
        sub = "Failed";
      } else if (asset.status === "waiting") sub = `Gate ${asset.gate}`;
      else if (asset.status === "stopped") sub = "Stopped";
      else if (asset.status === "running") sub = `${Math.round(asset.progress * 100)}%`;
      else sub = "Queued";
    }
    const isCurrent = i === current && asset.status !== "done";
    const onClick = viewable(asset, stage)
      ? () => setViewing(viewing === stage ? null : stage)
      : isCurrent && viewing
        ? () => setViewing(null)
        : undefined;
    return { label: STAGE_LABEL[stage], sub, state, onClick, selected: viewing ? viewing === stage : isCurrent };
  });
}

/** A finished step, shown again read-only. */
function PastStep({ asset, stage, godot, onBack }: { asset: Asset; stage: Stage; godot: boolean; onBack: () => void }) {
  return (
    <Stack gap={4}>
      <Notice title={`Looking back at ${STAGE_LABEL[stage].toLowerCase()}`}>
        <Row justify="between">
          <Text small>This step is finished; nothing here can be changed.</Text>
          <Button size="sm" onClick={onBack}>Back to the current step</Button>
        </Row>
      </Notice>
      {stage === "prompt" && <Gate1 asset={asset} readOnly />}
      {stage === "mesh" && <Gate2 asset={asset} readOnly />}
      {stage === "rig" && <Gate3 asset={asset} godot={godot} readOnly />}
    </Stack>
  );
}

function Working({ asset }: { asset: Asset }) {
  const stop = useApiMutation(() => api.stop(asset.id), [keys.assets, keys.asset(asset.id)]);
  const queued = asset.status === "queued";
  return (
    <Card
      title={queued ? "Queued" : `Working on ${STAGE_LABEL[asset.stage].toLowerCase()}`}
      actions={
        <Button size="sm" variant="danger" busy={stop.isPending} disabled={!queued && !asset.autopilot}
                title={queued ? "Take it off the queue" : "Finish this step, then wait at the gate"}
                onClick={() => stop.mutate(undefined)}>
          Stop
        </Button>
      }
    >
      <Stack gap={3}>
        <AssetStatus asset={asset} />
        {queued ? (
          <Text muted small>
            The GPU runs one job at a time; this starts when the worker is free. Stop takes it off the queue.
          </Text>
        ) : (
          <Text muted small>
            A running step can't be cut off safely; it finishes and waits at its gate.
            {asset.autopilot ? " Stop turns autopilot off so nothing continues after it." : ""}
          </Text>
        )}
        <ErrorNotice error={stop.error} />
      </Stack>
    </Card>
  );
}

export function AssetPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const asset = useAsset(id);
  const projects = useProjects();
  const profiles = useProfiles();
  const [deleting, setDeleting] = useState(false);
  const [varying, setVarying] = useState(false);
  const [viewing, setViewing] = useState<Stage | null>(null);
  const remove = useApiMutation(() => api.deleteAsset(id), [keys.assets]);

  if (asset.isPending) return <Loading />;
  if (!asset.data)
    return (
      <EmptyState
        title="Asset not found"
        body={asset.error instanceof Error ? asset.error.message : undefined}
        action={<LinkButton to="/">Back to assets</LinkButton>}
      />
    );

  const a = asset.data;
  const project = projects.data?.find((p) => p.id === a.project_id);
  const profile = profiles.data?.find((p) => p.id === a.profile_id);
  const viewKey = `${a.status}:${a.gate}:${JSON.stringify(a.attempts)}`;

  const godot = project?.engine === "godot";
  const shown = viewing && viewable(a, viewing) ? viewing : null;

  let body;
  if (shown) body = <PastStep key={`past:${shown}:${viewKey}`} asset={a} stage={shown} godot={godot} onBack={() => setViewing(null)} />;
  else if (a.status === "waiting" && a.gate === 1) body = <Gate1 key={viewKey} asset={a} />;
  else if (a.status === "waiting" && a.gate === 2) body = <Gate2 key={viewKey} asset={a} />;
  else if (a.status === "waiting" && a.gate === 3) body = <Gate3 key={viewKey} asset={a} godot={godot} />;
  else if (a.status === "done") body = <DoneView asset={a} />;
  else if (a.status === "failed") body = <FailedView asset={a} />;
  else if (a.status === "stopped") body = <StoppedView asset={a} />;
  else body = <Working asset={a} />;

  return (
    <Stack gap={4}>
      <PageHeader
        back={{ to: "/", label: "Assets" }}
        title={a.name}
        subtitle={
          <Row gap={2}>
            <Badge>{KIND_LABEL[a.kind]}</Badge>
            {a.archetype && <Badge>{label(a.archetype)}</Badge>}
            {a.slot && <Badge>{label(a.slot)}</Badge>}
            {a.weapon_class && <Badge>{label(a.weapon_class)}</Badge>}
            {project && <Text muted small>{project.name} · {label(project.engine)}</Text>}
          </Row>
        }
        actions={
          <Row gap={2}>
            <LinkButton to={`/new?from=${a.id}`} variant="ghost">More like this</LinkButton>
            {a.outputs.rig && a.status !== "running" && a.status !== "queued" && (
              <Button onClick={() => setVarying(true)}>Colour variant</Button>
            )}
            <Button variant="ghost" onClick={() => setDeleting(true)}>
              Delete asset
            </Button>
          </Row>
        }
      />
      <Stepper steps={steps(a, shown, setViewing)} />
      <ErrorNotice error={asset.error} />
      {body}
      <AutopilotCard asset={a} />
      {varying && <VariantDialog asset={a} onClose={() => setVarying(false)} />}
      <AssetDetails asset={a} projectName={project?.name} profileName={profile?.name} />
      <ConfirmDialog
        open={deleting}
        title={`Delete ${a.name}?`}
        body="The asset and all its generated files are removed. Files already exported into the game project stay."
        confirmLabel="Delete asset"
        busy={remove.isPending}
        error={remove.error}
        onClose={() => setDeleting(false)}
        onConfirm={() => remove.mutate(undefined, { onSuccess: () => navigate("/") })}
      />
    </Stack>
  );
}

/** Autopilot: on or off, and what it did at each gate. */
function AutopilotCard({ asset }: { asset: Asset }) {
  const toggle = useApiMutation((value: boolean) => api.setAutopilot(asset.id, value), [keys.assets, keys.asset(asset.id)]);
  const exporting = useApiMutation((value: boolean) => api.setAutopilot(asset.id, !!asset.autopilot, value),
                                   [keys.assets, keys.asset(asset.id)]);
  if (asset.status === "done" && !asset.autopilot_log?.length) return null;
  return (
    <Card
      title="Autopilot"
      subtitle="Judges each picture and mesh against the description with the local vision model: approves what passes, redraws or rebuilds what doesn't, and stops at gate 3 unless export is on."
      actions={
        asset.status !== "done" && (
          <Button size="sm" variant={asset.autopilot ? "secondary" : "primary"} busy={toggle.isPending}
                  onClick={() => toggle.mutate(!asset.autopilot)}>
            {asset.autopilot ? "Turn off" : "Turn on"}
          </Button>
        )
      }
    >
      <Stack gap={2}>
        {asset.status !== "done" && (
          <Checkbox label="Export by itself when everything passes" checked={!!asset.autopilot_export}
                    onChange={(on) => exporting.mutate(on)} />
        )}
        {!asset.autopilot_log?.length && <Text small muted>{asset.autopilot ? "On. Nothing decided yet." : "Off."}</Text>}
        {asset.autopilot_log?.map((entry) => (
          <Row key={entry.at} gap={2}>
            <Badge tone={entry.action === "approved" ? "success" : entry.action === "stopped" ? "warning" : "info"}>
              Gate {entry.gate} · {entry.action}
            </Badge>
            <Text small muted>{entry.reason}</Text>
          </Row>
        ))}
        <ErrorNotice error={toggle.error} />
      </Stack>
    </Card>
  );
}

/** A colour variant: same mesh, rig and clips, recoloured texture. The preview applies the change
 *  with CSS filters (close to, not exactly, what the recolour does). */
function VariantDialog({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState(`${asset.name} variant`);
  const [hue, setHue] = useState(0);
  const [saturation, setSaturation] = useState(1);
  const [brightness, setBrightness] = useState(1);
  const [tint, setTint] = useState("#3355ff");
  const [tintStrength, setTintStrength] = useState(0);
  const make = useApiMutation(
    () => api.makeVariant(asset.id, { name, hue, saturation, brightness, tint, tint_strength: tintStrength }),
    [keys.assets],
  );
  const image = localUrl(asset.urls?.thumb ?? asset.urls?.chosen_image ?? null);
  const slider = (label: string, value: number, set: (v: number) => void, min: number, max: number, step: number) => (
    <Field label={`${label}: ${value}`}>
      {(id) => <Input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => set(Number(e.target.value))} />}
    </Field>
  );
  return (
    <Dialog
      open
      title={`Colour variant of ${asset.name}`}
      onClose={onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={make.isPending} onClick={() => make.mutate(undefined, { onSuccess: (v) => navigate(`/asset/${v.id}`) })}>
            Make the variant
          </Button>
        </>
      }
    >
      <Stack gap={3}>
        <Text small muted>
          Same mesh, skeleton and clips with the texture recoloured: ready in seconds at gate 3, and it fits the same gear.
        </Text>
        <Grid cols={2}>
          <Stack gap={2}>
            {image && (
              <img src={image} alt="Preview" style={{ width: "100%", borderRadius: 10,
                filter: `hue-rotate(${hue}deg) saturate(${saturation}) brightness(${brightness})` }} />
            )}
            <Text small faint>Preview (approximate; the tint is not shown).</Text>
          </Stack>
          <Stack gap={2}>
            <Field label="Name">{(id) => <Input id={id} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />}</Field>
            {slider("Hue (degrees)", hue, setHue, -180, 180, 5)}
            {slider("Saturation", saturation, setSaturation, 0, 2, 0.05)}
            {slider("Brightness", brightness, setBrightness, 0.3, 2, 0.05)}
            <Field label="Tint colour">{(id) => <Input id={id} type="color" value={tint} onChange={(e) => setTint(e.target.value)} />}</Field>
            {slider("Tint strength", tintStrength, setTintStrength, 0, 1, 0.05)}
          </Stack>
        </Grid>
        <ErrorNotice error={make.error} />
      </Stack>
    </Dialog>
  );
}
