import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import type { AnswerOf, IgnoreAnswer, JobsSelector } from "../types";
import { keys } from "./keys";
import { useRefreshJobs } from "./refresh";

export function useCatalogStatus() {
  return useQuery({
    queryKey: keys.catalogStatus,
    queryFn: ({ signal }) => api<AnswerOf<"/api/catalog/status", "get">>("/api/catalog/status", { signal }),
    refetchInterval: 30_000,
  });
}

/**
 * **Sync now**: ask the worker to read Bazarr again; it answers at once and reads in the background, so the library
 * is shown out of date now and again a few seconds later. `acceptShrink` lets that one read through the shrink guard
 * (the admin confirmed how many items it drops).
 */
export function useSyncCatalog() {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ["catalog"] });
  return useMutation({
    mutationFn: (options?: { acceptShrink?: boolean }) =>
      api<AnswerOf<"/api/catalog/sync", "post">>("/api/catalog/sync", { method: "POST", body: options?.acceptShrink ? { accept_shrink: true } : {} }),
    onSettled: () => {
      void refresh();
      window.setTimeout(() => void refresh(), 4_000);
    },
  });
}

export function useCatalog() {
  return useQuery({
    queryKey: keys.catalog,
    queryFn: ({ signal }) => api<AnswerOf<"/api/catalog", "get">>("/api/catalog", { signal }),
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  });
}

export function useMovies() {
  return useQuery({
    queryKey: keys.movies,
    queryFn: ({ signal }) => api<AnswerOf<"/api/catalog/movies", "get">>("/api/catalog/movies", { signal }),
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  });
}

export function useSeriesPage(id: number) {
  return useQuery({
    queryKey: keys.series(id),
    queryFn: ({ signal }) => api<AnswerOf<"/api/catalog/series/{p1}", "get">>(`/api/catalog/series/${id}`, { signal }),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const active = items.some((item) => item.targets.some((t) => t.job === "running" || t.job === "requested" || t.job === "waiting"));
      return active ? 15_000 : 120_000;
    },
    refetchOnWindowFocus: false,
  });
}

/** Version, model and age of each episode's file; poll densely only while the series has an open job (P18a). */
export function useSeriesUnits(storedId: number | null | undefined, active = false) {
  return useQuery({
    queryKey: keys.seriesUnits(storedId ?? 0),
    queryFn: ({ signal }) => api<AnswerOf<"/api/library/series/{p1}/units", "get">>(`/api/library/series/${storedId}/units`, { signal }),
    enabled: typeof storedId === "number",
    refetchInterval: active ? 30_000 : 180_000,
    refetchOnWindowFocus: false,
  });
}

/** Auto-request for one series saves the moment it is touched (UX §2). */
export function useSetAutoRequest(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (auto_request: "" | "on" | "off") =>
      api<AnswerOf<"/api/catalog/series/{p1}/policy", "put">>(`/api/catalog/series/${id}/policy`, { method: "PUT", body: { auto_request } }),
    onSettled: () => client.invalidateQueries({ queryKey: ["catalog"] }),
  });
}

type BootstrapAnswerOf = AnswerOf<"/api/catalog/series/{p1}/bootstrap", "put">;

/**
 * What letting a held series go would ask for: nothing changes, nothing is made (FR-15.12). The box of "Auto-request
 * remaining N…" shows it, so the button never spends without a price. A price is only true now: never reused.
 */
export function useBootstrapEstimate(id: number, opened: boolean) {
  return useQuery({
    queryKey: ["bootstrap-estimate", id],
    queryFn: ({ signal }) =>
      api<BootstrapAnswerOf>(`/api/catalog/series/${id}/bootstrap`, { method: "PUT", body: { released: true, estimate_only: true }, signal }),
    enabled: opened,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/**
 * Let `auto_request` go past the first episodes of a series whose glossary nobody has reviewed, and ask for the rest at
 * once (`released: true`), or hold it again (`released: false`), optionally pulling back what the policy queued and had
 * not started (`cancelQueuedPolicy`) (FR-15.12).
 */
export function useReleaseBootstrap(id: number) {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: ({ released, cancelQueuedPolicy }: { released: boolean; cancelQueuedPolicy?: boolean }) =>
      api<BootstrapAnswerOf>(`/api/catalog/series/${id}/bootstrap`, {
        method: "PUT",
        body: released ? { released: true } : { released: false, cancel_queued_policy: cancelQueuedPolicy === true },
      }),
    onSettled: () => void refresh(), // the rest may be in line already, or pulled back
    // The worker asks for the rest in the background and answers at once: read once more when it has made the jobs (R19-8).
    onSuccess: (answer) => {
      if (answer.started) window.setTimeout(() => void refresh(), 2_000);
    },
  });
}

/** Ignore or stop ignoring: a decision, not a request, so it costs nothing and needs no estimate. */
export function useIgnore() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: ({ ignore, ...selector }: { ignore: boolean } & JobsSelector) =>
      api<IgnoreAnswer>(ignore ? "/api/catalog/ignore" : "/api/catalog/unignore", {
        method: "POST",
        body: selector,
      }),
    onSettled: () => void refresh(),
  });
}
