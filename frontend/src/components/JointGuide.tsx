// Where each joint of a biped belongs: a hint per joint and a small front and side figure.
// Shown beside the joint editor at gate 3.

import { Grid, SectionTitle, Stack, Text } from "../ui/kit";

interface Hint {
  label: string;
  where: string;
  /** What goes wrong when it sits elsewhere. */
  wrong?: string;
}

const HINTS: Record<string, Hint> = {
  root: { label: "Root", where: "On the floor between the feet. Leave it where it is." },
  pelvis: {
    label: "Pelvis",
    where: "Middle of the hips, a little below the belt, halfway between belly and back.",
    wrong: "Too high and the whole upper body sways from the waist.",
  },
  spine_01: { label: "Lower spine", where: "Just above the pelvis, in the middle of the body seen from the side." },
  spine_02: { label: "Middle spine", where: "Halfway up the back, between belt and chest. Keep the spine dots evenly spaced." },
  spine_03: { label: "Upper spine", where: "Chest height, a bit closer to the back than the front." },
  neck_01: {
    label: "Neck",
    where: "Base of the neck, just above the collarbones, level with the tops of the shoulders.",
    wrong: "Placed at the face, the neck bends at eye level.",
  },
  Head: {
    label: "Head",
    where: "Base of the skull, about level with the jaw or ears, in the middle seen from the side. Not the top of the head or helmet.",
    wrong: "At the top of the helmet the head swings around its crown.",
  },
  clavicle: { label: "Collarbone", where: "Next to the neck at the top of the chest, a little in front of the shoulder blade." },
  upperarm: {
    label: "Shoulder",
    where: "Middle of the shoulder ball, under a shoulder pad, not on top of it.",
    wrong: "On top of the pad, the arm lifts the armour with it.",
  },
  lowerarm: {
    label: "Elbow",
    where: "Where the arm bends, usually about waist height, in the middle of the arm.",
  },
  hand: {
    label: "Wrist",
    where: "Where the forearm (or its armour) ends and the hand starts. Not the knuckles or fingertips.",
    wrong: "At the knuckles, the hand bends in the middle of the palm.",
  },
  thigh: { label: "Hip", where: "The hip socket: a bit below and to the side of the pelvis dot, where the leg starts." },
  calf: { label: "Knee", where: "Middle of the knee (or knee guard), in the middle of the leg seen from the side." },
  foot: { label: "Ankle", where: "The ankle bone: where the shin meets the top of the foot." },
  ball: {
    label: "Toe bend",
    where: "Where the toes bend: two-thirds of the way toward the toe, slightly above the floor, pointing straight forward.",
    wrong: "On the toe tip or turned outward, the feet roll and splay when walking.",
  },
};

/** The joint's name without its side (``hand_l`` → ``hand``). */
function jointBase(name: string): string {
  return name.replace(/_(l|r)$/, "");
}

// Figure points: [front x, front y, side x, side y] in a 240 x 250 box. Sided joints give the
// character's left (drawn on the viewer's right in front view); the right is mirrored.
const POINTS: Record<string, [number, number, number, number]> = {
  Head: [60, 40, 182, 40],
  neck_01: [60, 60, 180, 60],
  spine_03: [60, 82, 177, 82],
  spine_02: [60, 100, 176, 100],
  spine_01: [60, 116, 177, 116],
  pelvis: [60, 130, 179, 130],
  clavicle: [68, 64, 182, 64],
  upperarm: [80, 70, 180, 70],
  lowerarm: [86, 112, 178, 112],
  hand: [90, 144, 182, 144],
  thigh: [70, 138, 180, 138],
  calf: [72, 182, 183, 182],
  foot: [73, 224, 179, 224],
  ball: [75, 236, 194, 236],
};
const SIDED = new Set(["clavicle", "upperarm", "lowerarm", "hand", "thigh", "calf", "foot", "ball"]);
const BONES: [string, string][] = [
  ["Head", "neck_01"],
  ["neck_01", "spine_03"],
  ["spine_03", "spine_02"],
  ["spine_02", "spine_01"],
  ["spine_01", "pelvis"],
  ["spine_03", "clavicle"],
  ["clavicle", "upperarm"],
  ["upperarm", "lowerarm"],
  ["lowerarm", "hand"],
  ["pelvis", "thigh"],
  ["thigh", "calf"],
  ["calf", "foot"],
  ["foot", "ball"],
];

function front(name: string, mirrored: boolean): [number, number] {
  const [x, y] = POINTS[name];
  return [mirrored ? 120 - x : x, y];
}

function Figure({ picked, onPick }: { picked?: string; onPick: (base: string) => void }) {
  const dot = (base: string, x: number, y: number, key: string) => {
    const on = base === picked;
    return (
      <circle
        key={key}
        cx={x}
        cy={y}
        r={on ? 5 : 3.5}
        role="button"
        aria-label={HINTS[base]?.label ?? base}
        onClick={() => onPick(base)}
        style={{ cursor: "pointer", fill: on ? "var(--accent)" : "var(--info)", stroke: "var(--bg)", strokeWidth: 1 }}
      >
        <title>{HINTS[base]?.label ?? base}</title>
      </circle>
    );
  };
  const body = { fill: "none", stroke: "var(--border-strong)", strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const bone = { stroke: "var(--text-faint)", strokeWidth: 1 };
  const sides = [false, true];
  return (
    <svg viewBox="0 0 240 250" width="100%" style={{ maxWidth: 320, display: "block" }} aria-label="Where the joints go">
      {/* Front: a plain figure, arms slightly out. */}
      <g style={body}>
        <circle cx={60} cy={34} r={15} strokeWidth={2} />
        <path d="M44 66 Q60 58 76 66 L74 128 Q60 136 46 128 Z" strokeWidth={2} />
        <path d="M44 68 L34 112 L30 150 M76 68 L86 112 L90 150" strokeWidth={9} opacity={0.5} />
        <path d="M51 134 L48 182 L47 236 M69 134 L72 182 L73 236" strokeWidth={11} opacity={0.5} />
      </g>
      {/* Side: facing right. */}
      <g style={body}>
        <circle cx={182} cy={34} r={15} strokeWidth={2} />
        <path d="M168 66 Q180 58 192 66 L190 128 Q178 136 166 128 Z" strokeWidth={2} />
        <path d="M180 70 L178 112 L182 150" strokeWidth={9} opacity={0.5} />
        <path d="M179 134 L183 182 L179 226 L198 238" strokeWidth={11} opacity={0.5} />
      </g>
      {BONES.flatMap(([a, b]) =>
        (SIDED.has(b) ? sides : [false]).map((m) => {
          const [x1, y1] = front(a, m && SIDED.has(a));
          const [x2, y2] = front(b, m);
          return <line key={`f${a}${b}${m}`} x1={x1} y1={y1} x2={x2} y2={y2} style={bone} />;
        }),
      )}
      {BONES.map(([a, b]) => (
        <line key={`s${a}${b}`} x1={POINTS[a][2]} y1={POINTS[a][3]} x2={POINTS[b][2]} y2={POINTS[b][3]} style={bone} />
      ))}
      {Object.keys(POINTS).flatMap((base) =>
        (SIDED.has(base) ? sides : [false]).map((m) => {
          const [x, y] = front(base, m);
          return dot(base, x, y, `f${base}${m}`);
        }),
      )}
      {Object.entries(POINTS).map(([base, p]) => dot(base, p[2], p[3], `s${base}`))}
      <text x={60} y={249} textAnchor="middle" style={{ fill: "var(--text-faint)", fontSize: 9 }}>
        front
      </text>
      <text x={182} y={249} textAnchor="middle" style={{ fill: "var(--text-faint)", fontSize: 9 }}>
        side
      </text>
    </svg>
  );
}

/** The guide beside the joint editor: the figure, the picked joint's hint and the general rules. */
export function JointGuide({ picked, onPick }: { picked?: string; onPick: (name: string) => void }) {
  const base = picked ? jointBase(picked) : undefined;
  const hint = base ? HINTS[base] : undefined;
  return (
    <Grid cols={2} min={240}>
      <Figure picked={base} onPick={onPick} />
      <Stack gap={3}>
        {hint ? (
          <Stack gap={1}>
            <SectionTitle>
              {hint.label}
              {picked && picked !== base ? ` · ${picked.endsWith("_l") ? "left" : "right"}` : ""}
            </SectionTitle>
            <Text>{hint.where}</Text>
            {hint.wrong && (
              <Text small muted>
                {hint.wrong}
              </Text>
            )}
          </Stack>
        ) : (
          <Text muted>Click a joint on the model or in the figure to see where it belongs.</Text>
        )}
        <Stack gap={1}>
          <SectionTitle>Rules</SectionTitle>
          <Text small muted>
            Each dot is the point the body bends around: put it in the middle of the joint, inside the body, not on
            the armour's surface.
          </Text>
          <Text small muted>
            Every joint's arrow should point forward, where the face looks (green). An amber arrow is turned away:
            use Face forward, or Twist and drag its ring. Top view shows the arrows best.
          </Text>
          <Text small muted>
            Turn the model and check from the side too: a dot can look right from the front and sit at the front or
            back of the limb.
          </Text>
          <Text small muted>
            Fix only what is wrong, usually two to four joints. Work from the body outward: spine and neck, shoulders
            and hips, elbows and knees, then wrists and feet. Moving a joint carries the ones below it.
          </Text>
          <Text small muted>
            If a piece of the mesh moves with the wrong limb, that is the skinning, not the joints: moving dots won't
            fix it.
          </Text>
        </Stack>
      </Stack>
    </Grid>
  );
}
