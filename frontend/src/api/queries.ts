// All server state lives in TanStack Query; the WebSocket (live.ts) keeps it fresh.

import { QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
};

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
