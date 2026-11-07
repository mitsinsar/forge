import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Asset, type Stage } from "../api/client";
import { keys, useApiMutation, useAsset, useProfiles, useProjects } from "../api/queries";
import { AssetStatus } from "../components/AssetStatus";
import { AssetDetails, DoneView, FailedView, Gate1, Gate2, Gate3 } from "../components/Gates";
import { KIND_LABEL, STAGE_LABEL, label } from "../labels";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
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
  return (
    <Card title={asset.status === "queued" ? "Queued" : `Working on ${STAGE_LABEL[asset.stage].toLowerCase()}`}>
      <Stack gap={3}>
        <AssetStatus asset={asset} />
        {asset.status === "queued" && (
          <Text muted small>
            The GPU runs one job at a time; this starts when the worker is free.
          </Text>
        )}
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
          <Button variant="ghost" onClick={() => setDeleting(true)}>
            Delete asset
          </Button>
        }
      />
      <Stepper steps={steps(a, shown, setViewing)} />
      <ErrorNotice error={asset.error} />
      {body}
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
