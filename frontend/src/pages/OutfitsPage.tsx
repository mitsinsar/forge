// Outfits: a hero with gear and a weapon, previewed together in 3D. Picking an item fits it on the
// hero (a worker job, kept per hero), so items can be swapped between heroes freely.

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, localUrl, type Asset, type Job, type Outfit } from "../api/client";
import { keys, useApiMutation, useAssets, useMeta, useProjects } from "../api/queries";
import { ModelViewer } from "../components/Viewer";
import { label } from "../labels";
import {
  Badge,
  Box,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Field,
  Grid,
  Input,
  LinkButton,
  Loading,
  MediaCard,
  PageHeader,
  Row,
  SectionTitle,
  Select,
  Stack,
  Tabs,
  Text,
} from "../ui/kit";

const thumb = (asset: Asset) => localUrl(asset.urls?.thumb ?? null) ?? null;

/** The item's GLB fitted on this hero: its own gate 3 result when it was fitted there, else a kept fit. */
function fittedUrl(item: Asset, heroId: string): string | null {
  if (item.outputs.rig?.wearer?.id === heroId) return localUrl(item.urls?.animated ?? null) ?? null;
  return localUrl(item.urls?.fits?.[heroId] ?? null) ?? null;
}

export function OutfitsPage() {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const meta = useMeta();
  const projects = useProjects();
  const projectId = params.get("project") ?? projects.data?.[0]?.id ?? "";
  const assets = useAssets({ project_id: projectId || undefined });
  const wearers = useQuery({
    queryKey: ["wearers", projectId],
    queryFn: () => api.wearers(projectId),
    enabled: !!projectId,
  });
  const outfits = useQuery({ queryKey: keys.outfits(projectId), queryFn: () => api.outfits(projectId), enabled: !!projectId });
  const fitJobs = useQuery({
    queryKey: ["jobs", "fit", projectId],
    queryFn: () => api.jobs({ kind: "fit" }),
    refetchInterval: (query) =>
      (query.state.data as Job[] | undefined)?.some((j) => j.status === "queued" || j.status === "running") ? 1500 : false,
  });

  const [heroId, setHeroId] = useState("");
  const [chosen, setChosen] = useState<Record<string, string>>({}); // slot (or "weapon") -> item id
  const [clip, setClip] = useState("walk");
  const [name, setName] = useState("");
  const [outfitId, setOutfitId] = useState<string | null>(null);

  const heroes = wearers.data ?? [];
  const hero = assets.data?.find((a) => a.id === (heroId || heroes[0]?.id));
  useEffect(() => setHeroId(""), [projectId]);

  const items = useMemo(
    () => (assets.data ?? []).filter((a) => (a.kind === "gear" || a.kind === "weapon") && a.outputs.mesh),
    [assets.data],
  );
  const groups: [string, Asset[]][] = [
    ...(meta.data?.gear_slots ?? []).map((slot): [string, Asset[]] => [slot, items.filter((i) => i.kind === "gear" && i.slot === slot)]),
    ["weapon", items.filter((i) => i.kind === "weapon")],
  ];

  // A finished fit job reloads the items (their fits).
  const pending = (fitJobs.data ?? []).filter((j) => j.status === "queued" || j.status === "running");
  const pendingKey = pending.map((j) => j.id).join("|");
  useEffect(() => {
    client.invalidateQueries({ queryKey: keys.assets });
  }, [pendingKey, client]);

  const fit = useApiMutation(({ item, wearer }: { item: string; wearer: string }) => api.fit(item, wearer), [["jobs", "fit", projectId]]);
  const pick = (group: string, item: Asset | null) => {
    setChosen((current) => {
      const next = { ...current };
      if (item) next[group] = item.id;
      else delete next[group];
      return next;
    });
    if (item && hero && !fittedUrl(item, hero.id) && !pending.some((j) => j.target_id === item.id))
      fit.mutate({ item: item.id, wearer: hero.id });
  };
  // Switching heroes fits what is worn on the new one.
  useEffect(() => {
    if (!hero) return;
    for (const id of Object.values(chosen)) {
      const item = items.find((i) => i.id === id);
      if (item && !fittedUrl(item, hero.id) && !pending.some((j) => j.target_id === item.id && j.params.wearer_id === hero.id))
        fit.mutate({ item: item.id, wearer: hero.id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hero?.id]);

  const worn = Object.values(chosen).map((id) => items.find((i) => i.id === id)).filter((i): i is Asset => !!i);
  const extras = hero ? worn.map((i) => fittedUrl(i, hero.id)).filter((u): u is string => !!u) : [];
  const fitting = hero ? worn.filter((i) => !fittedUrl(i, hero.id)) : [];
  const failed = (fitJobs.data ?? []).filter((j) => j.status === "failed" && worn.some((i) => i.id === j.target_id));

  const save = useApiMutation(
    () =>
      outfitId
        ? api.updateOutfit(outfitId, { name, hero_id: hero!.id, items: Object.values(chosen) })
        : api.createOutfit({ project_id: projectId, name, hero_id: hero!.id, items: Object.values(chosen) }),
    [keys.outfits(projectId)],
  );
  const remove = useApiMutation((id: string) => api.deleteOutfit(id), [keys.outfits(projectId)]);
  const load = (outfit: Outfit) => {
    setOutfitId(outfit.id);
    setName(outfit.name);
    setHeroId(outfit.hero_id);
    const next: Record<string, string> = {};
    for (const id of outfit.items) {
      const item = items.find((i) => i.id === id);
      if (item) next[item.kind === "weapon" ? "weapon" : item.slot ?? "chest"] = id;
    }
    setChosen(next);
  };

  if (projects.isPending || assets.isPending) return <Loading />;
  return (
    <Stack gap={4}>
      <PageHeader
        title="Outfits"
        subtitle="Dress a hero in gear and a weapon and see them together. Items are fitted on each hero you try them on."
      />
      <Row gap={3}>
        <Box minWidth={220}>
          <Field label="Project">
            {(id) => (
              <Select id={id} value={projectId} onChange={(e) => setParams({ project: e.target.value })}
                      options={(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))} />
            )}
          </Field>
        </Box>
      </Row>

      {!heroes.length ? (
        <EmptyState
          title="No rigged heroes in this project"
          body="Outfits need a biped hero that has been through gate 3. Heroes rigged before outfits existed need a re-rig."
          action={<LinkButton to="/new" variant="primary">New asset</LinkButton>}
        />
      ) : (
        <Grid cols={2}>
          <Stack gap={4}>
            <Card title={hero?.name ?? "Hero"} subtitle={`${worn.length} item${worn.length === 1 ? "" : "s"} worn`}>
              <Stack gap={3}>
                {hero?.outputs.rig && (
                  <Tabs label="Clip" value={clip} onChange={setClip}
                        items={hero.outputs.rig.clips.map((c) => ({ value: c.name, label: label(c.name) }))} />
                )}
                {hero?.urls?.animated && <ModelViewer url={localUrl(hero.urls.animated)!} clip={clip} extras={extras} />}
                {fitting.length > 0 && (
                  <Text small muted>Fitting {fitting.map((i) => i.name).join(", ")} on {hero?.name}… it appears when it is ready.</Text>
                )}
                {failed.map((j) => (
                  <Text key={j.id} small muted>Fitting failed: {j.error}</Text>
                ))}
                <ErrorNotice error={fit.error} />
              </Stack>
            </Card>
            <Card title="Saved outfits">
              <Stack gap={3}>
                <Row align="top">
                  <Box minWidth={220}>
                    <Field label="Name">
                      {(id) => <Input id={id} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />}
                    </Field>
                  </Box>
                  <Button variant="primary" disabled={!name.trim() || !hero} busy={save.isPending}
                          onClick={() => save.mutate(undefined, { onSuccess: (o) => setOutfitId(o.id) })}>
                    {outfitId ? "Save changes" : "Save outfit"}
                  </Button>
                  {outfitId && <Button variant="ghost" onClick={() => { setOutfitId(null); setName(""); }}>New outfit</Button>}
                </Row>
                <ErrorNotice error={save.error ?? remove.error} />
                {(outfits.data ?? []).length === 0 ? (
                  <Text small muted>None yet.</Text>
                ) : (
                  <Stack gap={2}>
                    {outfits.data!.map((o) => (
                      <Row key={o.id} justify="between">
                        <Row gap={2}>
                          <Text strong>{o.name}</Text>
                          <Text small faint>
                            {assets.data?.find((a) => a.id === o.hero_id)?.name ?? "?"} · {o.items.length} items
                          </Text>
                        </Row>
                        <Row gap={1}>
                          <Button size="sm" onClick={() => load(o)}>Wear</Button>
                          <Button size="sm" variant="danger" onClick={() => remove.mutate(o.id)}>Delete</Button>
                        </Row>
                      </Row>
                    ))}
                  </Stack>
                )}
              </Stack>
            </Card>
          </Stack>

          <Stack gap={4}>
            <Card title="Hero">
              <Grid min={110}>
                {heroes.map((h) => {
                  const asset = assets.data?.find((a) => a.id === h.id);
                  return (
                    <MediaCard key={h.id} image={asset ? thumb(asset) : null} title={h.name}
                               subtitle={h.height ? `${h.height} m` : undefined}
                               selected={hero?.id === h.id} onClick={() => setHeroId(h.id)} />
                  );
                })}
              </Grid>
            </Card>
            {groups.map(([group, list]) => (
              <Card key={group} title={label(group)}>
                {list.length === 0 ? (
                  <Text small muted>No {group === "weapon" ? "weapons" : `${label(group).toLowerCase()} gear`} with a mesh yet.</Text>
                ) : (
                  <Grid min={110}>
                    <MediaCard image={null} empty="Nothing" title="None" selected={!chosen[group]} onClick={() => pick(group, null)} />
                    {list.map((item) => {
                      const ready = hero ? !!fittedUrl(item, hero.id) : false;
                      const busy = pending.some((j) => j.target_id === item.id);
                      return (
                        <MediaCard
                          key={item.id}
                          image={thumb(item)}
                          title={item.name}
                          subtitle={label(item.weapon_class ?? item.slot)}
                          selected={chosen[group] === item.id}
                          badges={busy ? <Badge tone="accent" dot pulse>Fitting</Badge> : ready ? undefined : <Badge>Not fitted</Badge>}
                          onClick={() => pick(group, item)}
                        />
                      );
                    })}
                  </Grid>
                )}
              </Card>
            ))}
            <SectionTitle>Items are fitted on the base body at their own gate 3; here they are fitted on the hero you pick.</SectionTitle>
          </Stack>
        </Grid>
      )}
    </Stack>
  );
}
