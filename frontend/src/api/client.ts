// Typed client for the assetgen backend (all routes under /api; see backend/README.md).

export type Engine = "unity" | "godot";
export type Kind = "hero" | "monster" | "boss" | "gear" | "weapon";
export type BaseModel = "qwen-image" | "sdxl" | "flux";
export type CameraPreset = "side" | "three_quarter" | "topdown_4" | "topdown_8";
export type Slot = "head" | "chest" | "legs" | "hands" | "feet" | "cape";
export type WeaponClass = "one_handed" | "two_handed" | "bow" | "staff";
export type Socket = "weapon_r" | "weapon_l";
export type AssetStatus = "queued" | "running" | "waiting" | "done" | "failed";
export type Stage = "prompt" | "mesh" | "rig" | "export";
export type Gate = 1 | 2 | 3;

export type PerKind = Record<Kind, number>;

export interface LoraRef {
  id: string;
  weight: number;
}

export interface RenderSettings {
  look: "hd" | "pixel";
  fps: number;
  supersample: number;
  palette_size: number;
  frame_size: PerKind;
}

export interface Budgets {
  triangles: PerKind;
  texture: PerKind;
  lod_ratios: number[];
}

export interface ProfileSettings {
  base_model: BaseModel;
  prompt_suffix: string;
  negative_prompt: string;
  loras: LoraRef[];
  camera_preset: CameraPreset;
  /** Even out the light and shade Hunyuan3D bakes into the albedo. */
  delight: boolean;
  candidates: number;
  clips: string[];
  biped_extra_clips: string[];
  render: RenderSettings;
  budgets: Budgets;
}

export interface ToolStatus {
  tool: string;
  name: string;
  mode: "mock" | "real";
  /** null for mock tools, which have nothing to check. */
  ok: boolean | null;
  detail: string;
}

export interface Meta {
  version: string;
  adapters: Record<string, "mock" | "real">;
  profile_defaults: ProfileSettings;
  kinds: Kind[];
  engines: Engine[];
  base_models: BaseModel[];
  camera_presets: CameraPreset[];
  camera_directions: Record<CameraPreset, string[]>;
  sprite_looks: ("hd" | "pixel")[];
  gear_slots: Slot[];
  weapon_classes: WeaponClass[];
  weapon_attacks: Record<WeaponClass, string[]>;
  stages: Stage[];
  max_reference_images: number;
}

export interface Archetype {
  id: string;
  name: string;
  description: string;
  rig_type: string;
  rest_pose: string;
}

export interface Project {
  id: string;
  name: string;
  engine: Engine;
  path: string;
  default_profile_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectInput {
  name: string;
  engine: Engine;
  path: string;
  default_profile_id?: string | null;
}

export interface ReferenceImage {
  id: string;
  file: string;
  filename: string;
  uploaded_at: string;
  url: string;
}

export interface Profile extends ProfileSettings {
  id: string;
  name: string;
  project_id: string | null;
  reference_images: ReferenceImage[];
  created_at: string;
  updated_at: string;
}

export type ProfileInput = ProfileSettings & { name: string; project_id: string | null };

export interface Lora {
  id: string;
  name: string;
  path: string;
  base_model: BaseModel;
  tags: string[];
  licence: string;
  commercial_use: boolean;
  source_url: string;
  uploaded: boolean;
  file_exists: boolean;
}

export interface LoraFields {
  name: string;
  base_model: BaseModel;
  tags: string[];
  licence: string;
  commercial_use: boolean;
  source_url: string;
}

export interface Lod {
  level: number;
  ratio: number;
  triangles: number;
}

export interface Clip {
  name: string;
  duration: number;
  loop: boolean;
}

export interface Grip {
  socket?: Socket;
  position?: [number, number, number];
  rotation?: [number, number, number];
}

export interface SpritePreview {
  camera_preset: CameraPreset;
  directions: string[];
  mirrored: Record<string, string>;
  fps: number;
  frame_size: number;
  files: Record<string, Record<string, string[]>>;
}

export interface AssetOutputs {
  prompt?: {
    /** "upload": the single candidate is the user's own image; no image model ran. */
    source?: "upload";
    expansion: { archetype: string; prompt: string; negative_prompt: string; flags?: string[] };
    prompt: string;
    negative_prompt: string;
    base_model: BaseModel | null;
    attempt: number;
    /** Lines from the image stage: LoRAs used, reference images applied or skipped. */
    log?: string[];
    candidates: string[];
  };
  mesh?: { glb: string; texture: string; triangle_budget: number; lods: Lod[]; delight?: boolean };
  rig?: {
    glb: string;
    clips: Clip[];
    preview_clips: string[];
    rig_type: string;
    skeleton: { joints: number; fit_score: number; fallback: boolean };
    grip: Grip | null;
    sprites?: SpritePreview;
  };
  export?: {
    engine: Engine;
    name: string;
    folder: string;
    zip: string;
    installed_to: string | null;
    project_relative: string;
    files: string[];
    exported_at: string;
  };
}

export interface Asset {
  id: string;
  project_id: string;
  profile_id: string | null;
  kind: Kind;
  name: string;
  description: string;
  archetype: string | null;
  archetype_override: string | null;
  slot: Slot | null;
  weapon_class: WeaponClass | null;
  grip: Grip | null;
  /** "upload" when made from the user's own image; missing on older assets. */
  source?: "generated" | "upload";
  status: AssetStatus;
  stage: Stage;
  gate: Gate | null;
  step: string;
  progress: number;
  error: string | null;
  notes: { gate?: number; stage?: string; note: string; at?: string }[];
  chosen_candidate: number | null;
  attempts: Partial<Record<Stage, number>>;
  outputs: AssetOutputs;
  training_candidate: boolean;
  created_at: string;
  updated_at: string;
  urls?: {
    candidates: string[];
    chosen_image: string | null;
    mesh: string | null;
    texture: string | null;
    animated: string | null;
    zip: string | null;
    export_files: string[];
  };
  settings?: ProfileSettings;
}

export interface AssetInput {
  project_id: string;
  kind: Kind;
  description: string;
  name?: string;
  archetype?: string;
  slot?: Slot;
  weapon_class?: WeaponClass;
  profile_id?: string;
  /** Per-asset overrides of style profile fields. */
  overrides?: { budgets?: { triangles?: Partial<PerKind> } };
}

export interface AssetFilters {
  project_id?: string;
  kind?: Kind;
  status?: AssetStatus;
}

export interface RejectInput {
  note?: string;
  archetype?: string;
  grip?: Grip;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail))
        message = data.detail
          .map((d: { loc?: string[]; msg: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
          .join("; ");
    } catch {
      // not JSON; keep the status text
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(params: object): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null);
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}` : "";
}

/** The backend returns absolute URLs; keep only the path so they work through any host or proxy. */
export function localUrl(url: string): string;
export function localUrl(url: string | null | undefined): string | null;
export function localUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  const parsed = new URL(url, window.location.href);
  return parsed.pathname + parsed.search;
}

/** URL of a file the pipeline wrote, from its path relative to the asset folder. */
export function assetFileUrl(assetId: string, relPath: string): string {
  return `/api/assets/${assetId}/files/${relPath.split("/").map(encodeURIComponent).join("/")}`;
}

export const api = {
  meta: () => request<Meta>("GET", "/meta"),
  tools: () => request<ToolStatus[]>("GET", "/tools"),
  archetypes: () => request<Archetype[]>("GET", "/archetypes"),

  projects: () => request<Project[]>("GET", "/projects"),
  createProject: (input: ProjectInput) => request<Project>("POST", "/projects", input),
  updateProject: (id: string, input: Partial<ProjectInput>) => request<Project>("PATCH", `/projects/${id}`, input),
  deleteProject: (id: string) => request<void>("DELETE", `/projects/${id}`),

  profiles: (projectId?: string) => request<Profile[]>("GET", `/profiles${query({ project_id: projectId })}`),
  profile: (id: string) => request<Profile>("GET", `/profiles/${id}`),
  createProfile: (input: ProfileInput) => request<Profile>("POST", "/profiles", input),
  updateProfile: (id: string, input: Partial<ProfileInput>) => request<Profile>("PATCH", `/profiles/${id}`, input),
  deleteProfile: (id: string) => request<void>("DELETE", `/profiles/${id}`),
  uploadReference: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Profile>("POST", `/profiles/${id}/references`, form);
  },
  deleteReference: (id: string, refId: string) => request<Profile>("DELETE", `/profiles/${id}/references/${refId}`),

  loras: (baseModel?: BaseModel) => request<Lora[]>("GET", `/loras${query({ base_model: baseModel })}`),
  registerLora: (input: LoraFields & { path: string }) => request<Lora>("POST", "/loras", input),
  uploadLora: (file: File, fields: LoraFields) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", fields.name);
    form.append("base_model", fields.base_model);
    form.append("tags", fields.tags.join(","));
    form.append("licence", fields.licence);
    form.append("commercial_use", String(fields.commercial_use));
    form.append("source_url", fields.source_url);
    return request<Lora>("POST", "/loras/upload", form);
  },
  updateLora: (id: string, input: Partial<LoraFields & { path: string }>) =>
    request<Lora>("PATCH", `/loras/${id}`, input),
  deleteLora: (id: string) => request<void>("DELETE", `/loras/${id}`),

  assets: (filters: AssetFilters) => request<Asset[]>("GET", `/assets${query(filters)}`),
  asset: (id: string) => request<Asset>("GET", `/assets/${id}`),
  createAsset: (input: AssetInput) => request<Asset>("POST", "/assets", input),
  createAssetFromImage: (input: AssetInput, file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("asset", JSON.stringify(input));
    return request<Asset>("POST", "/assets/from-image", form);
  },
  deleteAsset: (id: string) => request<void>("DELETE", `/assets/${id}`),
  approve: (id: string, gate: Gate, candidate?: number) =>
    request<Asset>("POST", `/assets/${id}/gates/${gate}/approve`, candidate ? { candidate } : {}),
  reject: (id: string, gate: Gate, input: RejectInput) =>
    request<Asset>("POST", `/assets/${id}/gates/${gate}/reject`, input),
  retry: (id: string) => request<Asset>("POST", `/assets/${id}/retry`),
  reexport: (id: string) => request<Asset>("POST", `/assets/${id}/export`),
  setTrainingCandidate: (id: string, value: boolean) =>
    request<Asset>("PUT", `/assets/${id}/training-candidate`, { value }),
};
