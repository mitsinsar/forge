// Human-readable names for the backend's vocabularies.

import type { AssetStatus, Kind, Stage } from "./api/client";

const SPECIAL: Record<string, string> = {
  "qwen-image": "Qwen-Image",
  sdxl: "SDXL",
  flux: "Flux",
  hd: "HD",
  unity: "Unity",
  godot: "Godot",
  topdown_4: "Top-down (4 dir)",
  topdown_8: "Top-down (8 dir)",
  three_quarter: "Three-quarter",
  one_handed: "One-handed",
  two_handed: "Two-handed",
};

/** "one_handed" → "One-handed", "attack_1" → "Attack 1", "down_right" → "Down right". */
export function label(value: string | null | undefined): string {
  if (!value) return "";
  if (SPECIAL[value]) return SPECIAL[value];
  const words = value.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const options = (values: readonly string[]) => values.map((value) => ({ value, label: label(value) }));

export const KIND_LABEL: Record<Kind, string> = {
  hero: "Hero",
  monster: "Monster",
  boss: "Boss",
  gear: "Gear",
  weapon: "Weapon",
};

export const STATUS_LABEL: Record<AssetStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting: "Waiting",
  done: "Done",
  failed: "Failed",
};

export const STAGE_LABEL: Record<Stage, string> = {
  prompt: "Preview",
  mesh: "Mesh",
  rig: "Animate",
  export: "Export",
};

export const isCharacter = (kind: Kind) => kind !== "gear" && kind !== "weapon";

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}
