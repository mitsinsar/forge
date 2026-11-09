// All server state lives in TanStack Query; the WebSocket (live.ts) keeps it fresh.

import { QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Job } from "./client";
import { api, type AssetFilters, type BaseModel } from "./client";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: false },
  },
});

export const keys = {
  meta: ["meta"] as const,
  tools: ["tools"] as const,
  archetypes: ["archetypes"] as const,
  projects: ["projects"] as const,
  profiles: ["profiles"] as const,
  profile: (id: string) => ["profile", id] as const,
  loras: ["loras"] as const,
  assets: ["assets"] as const,
  assetList: (filters: AssetFilters) => ["assets", "list", filters] as const,
  asset: (id: string) => ["asset", id] as const,
  jobs: (targetId: string, kind?: string) => ["jobs", targetId, kind ?? ""] as const,
  outfits: (projectId?: string) => ["outfits", projectId ?? ""] as const,
  motions: ["motions"] as const,
  sets: ["sets"] as const,
  set: (id: string) => ["set", id] as const,
};

/** The latest side job of this kind for a target, polled while it waits or runs. When it finishes,
 *  ``onDone`` runs (to reload what it made). */
export function useLatestJob(targetId: string, kind: Job["kind"], onDone?: () => void) {
  const client = useQueryClient();
  return useQuery({
    queryKey: keys.jobs(targetId, kind),
    queryFn: async () => {
      const [job] = await api.jobs({ target_id: targetId, kind });
      const previous = client.getQueryData<Job | null>(keys.jobs(targetId, kind));
      if (job && previous && previous.id === job.id && previous.status !== job.status &&
          (job.status === "done" || job.status === "failed")) onDone?.();
      return job ?? null;
    },
    refetchInterval: (query) => {
      const job = query.state.data as Job | null | undefined;
      return job && (job.status === "queued" || job.status === "running") ? 1500 : false;
    },
  });
}

export const useMeta = () => useQuery({ queryKey: keys.meta, queryFn: api.meta, staleTime: Infinity });
/** Whether the tools in real mode answer; checked again every 30 seconds. */
export const useTools = () =>
  useQuery({ queryKey: keys.tools, queryFn: api.tools, refetchInterval: 30_000, staleTime: 0 });
export const useArchetypes = () =>
  useQuery({ queryKey: keys.archetypes, queryFn: api.archetypes, staleTime: Infinity });
export const useProjects = () => useQuery({ queryKey: keys.projects, queryFn: api.projects });
export const useProfiles = () => useQuery({ queryKey: keys.profiles, queryFn: () => api.profiles() });
export const useProfile = (id: string | undefined) =>
  useQuery({ queryKey: keys.profile(id ?? ""), queryFn: () => api.profile(id!), enabled: !!id });
export const useLoras = (baseModel?: BaseModel) =>
  useQuery({
    queryKey: keys.loras,
    queryFn: () => api.loras(),
    select: (loras) => (baseModel ? loras.filter((l) => l.base_model === baseModel) : loras),
  });
/** Skill animations, polled while one waits or runs (the worker doesn't send live events for them). */
export const useMotions = () =>
  useQuery({
    queryKey: keys.motions,
    queryFn: api.motions,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((m) => m.status === "queued" || m.status === "running") ? 1500 : false,
  });
export const useSets = () => useQuery({ queryKey: keys.sets, queryFn: api.sets });
/** A character set, polled while it builds. */
export const useSet = (id: string) =>
  useQuery({
    queryKey: keys.set(id),
    queryFn: () => api.set(id),
    refetchInterval: (query) => (query.state.data?.status === "building" ? 2000 : false),
  });
export const useAssets = (filters: AssetFilters) =>
  useQuery({ queryKey: keys.assetList(filters), queryFn: () => api.assets(filters) });
export const useAsset = (id: string) => useQuery({ queryKey: keys.asset(id), queryFn: () => api.asset(id) });

/** A mutation that refreshes the given query groups when it succeeds. */
export function useApiMutation<TArgs, TResult>(
  fn: (args: TArgs) => Promise<TResult>,
  invalidate: readonly (readonly string[])[],
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => {
      await Promise.all(invalidate.map((queryKey) => client.invalidateQueries({ queryKey })));
    },
  });
}
