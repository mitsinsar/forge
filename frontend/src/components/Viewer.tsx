// The 3D viewer, loaded only when a screen shows it (three.js is large).

import { lazy, Suspense, type ComponentProps } from "react";
import { StageMessage } from "../ui/kit";

const LazyModelViewer = lazy(() => import("../viewers/ModelViewer").then((m) => ({ default: m.ModelViewer })));

export function ModelViewer(props: ComponentProps<typeof LazyModelViewer>) {
  return (
    <Suspense fallback={<StageMessage>Loading viewer…</StageMessage>}>
      <LazyModelViewer {...props} />
    </Suspense>
  );
}

const LazySkillStage = lazy(() => import("../viewers/SkillStage").then((m) => ({ default: m.SkillStage })));

/** A hero playing a skill animation with the skill's effect (3D). */
export function SkillStage(props: ComponentProps<typeof LazySkillStage>) {
  return (
    <Suspense fallback={<StageMessage>Loading viewer…</StageMessage>}>
      <LazySkillStage {...props} />
    </Suspense>
  );
}
