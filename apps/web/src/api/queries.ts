import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AuthMe,
  CatalogOverview,
  CatalogStatus,
  Correction,
  CuesPage,
  JobsAnswer,
  JobsSelector,
  LibraryUnits,
  MoviesPage,
  SeriesPage,
  StatusResponse,
  UnitDetail,
} from "./types";

export const keys = {
  unit: (id: number) => ["unit", id] as const,
  series: (id: number) => ["catalog", "series", id] as const,
  seriesUnits: (storedId: number) => ["library", "series", storedId, "units"] as const,
  status: ["status"] as const,
  catalogStatus: ["catalog", "status"] as const,
  me: ["auth", "me"] as const,
  catalog: ["catalog", "overview"] as const,
  movies: ["catalog", "movies"] as const,
};

export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: ({ signal }) => api<StatusResponse>("/api/status", { signal }),
    refetchInterval: 15_000,
  });
}

export function useCatalogStatus() {
  return useQuery({
    queryKey: keys.catalogStatus,
    queryFn: ({ signal }) => api<CatalogStatus>("/api/catalog/status", { signal }),
    refetchInterval: 30_000,
  });
}

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: ({ signal }) => api<AuthMe>("/api/auth/me", { signal }),
    staleTime: Infinity,
  });
}

/** **Sync now**: read Bazarr again, then everything that shows the library is out of date. */
export function useSyncCatalog() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<unknown>("/api/catalog/sync", { method: "POST", body: {} }),
    onSettled: () => client.invalidateQueries({ queryKey: ["catalog"] }),
  });
}

export function useSignOut() {
  return useMutation({
    mutationFn: () => api<unknown>("/api/auth/logout", { method: "POST", body: {} }),
    onSuccess: () => window.location.assign("/login"),
  });
}

export function useCatalog() {
  return useQuery({
    queryKey: keys.catalog,
    queryFn: ({ signal }) => api<CatalogOverview>("/api/catalog", { signal }),
    refetchInterval: 15_000,
  });
}

export function useMovies() {
  return useQuery({
    queryKey: keys.movies,
    queryFn: ({ signal }) => api<MoviesPage>("/api/catalog/movies", { signal }),
    refetchInterval: 15_000,
  });
}

/** A button that spends model calls says what it is about: translate these, or retry that failed job. */
export type CostRequest = { selector: JobsSelector } | { retry: { job_id: string } };

/** What a request would cost (UX §6.4): nothing is made, saved or called. */
export function useEstimate(request: CostRequest | null) {
  return useQuery({
    queryKey: ["estimate", request],
    queryFn: ({ signal }) =>
      request && "retry" in request
        ? api<JobsAnswer>("/api/queue/retry", { method: "POST", body: { ...request.retry, estimate_only: true }, signal })
        : api<JobsAnswer>("/api/jobs", { method: "POST", body: { ...request?.selector, estimate_only: true }, signal }),
    enabled: request !== null,
    // A price is only true now: never reuse the one from the last time the box was open.
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** Whatever changes a job changes every number and label that counts or shows jobs. */
function useRefreshJobs() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
      client.invalidateQueries({ queryKey: keys.status }),
    ]);
}

/** Confirm: make the jobs (or put the failed one back in line); `count` is how many are now in line. */
export function useRequestJobs() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: async (request: CostRequest): Promise<{ count: number }> => {
      if ("retry" in request) {
        const answer = await api<{ ok: boolean }>("/api/queue/retry", { method: "POST", body: request.retry });
        if (!answer.ok) throw new Error("The job could not be retried: it is not failed any more, or the file already has a job.");
        return { count: 1 };
      }
      const answer = await api<JobsAnswer>("/api/jobs", { method: "POST", body: request.selector });
      return { count: answer.count ?? 0 };
    },
    onSettled: () => void refresh(),
  });
}

/** Cancel a requested or waiting job, or ask a running one to stop at the next batch (no model call, no estimate). */
export function useStopJob() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: (jobId: string) => api<{ ok: boolean }>("/api/queue/stop", { method: "POST", body: { job_id: jobId } }),
    onSettled: () => void refresh(),
  });
}

export function useSeriesPage(id: number) {
  return useQuery({
    queryKey: keys.series(id),
    queryFn: ({ signal }) => api<SeriesPage>(`/api/catalog/series/${id}`, { signal }),
    refetchInterval: 10_000,
  });
}

/** Version, model and age of each episode's file; only series the worker keeps (a stored id) have them. */
export function useSeriesUnits(storedId: number | null | undefined) {
  return useQuery({
    queryKey: keys.seriesUnits(storedId ?? 0),
    queryFn: ({ signal }) => api<LibraryUnits>(`/api/library/series/${storedId}/units`, { signal }),
    enabled: typeof storedId === "number",
    refetchInterval: 30_000,
  });
}

// -- one episode or movie -----------------------------------------------------------------------------------------

export function useUnit(id: number) {
  return useQuery({
    queryKey: keys.unit(id),
    queryFn: ({ signal }) => api<UnitDetail>(`/api/units/${id}`, { signal }),
    refetchInterval: 15_000,
  });
}

export interface CueQuery {
  lang: string;
  q: string;
  offset: number;
  /** A player time (`00:23:10`): the page that holds it, with `focus` on the cue. */
  at: string;
  limit: number;
}

export function useCues(id: number, query: CueQuery, enabled: boolean) {
  const params = new URLSearchParams({ lang: query.lang, offset: String(query.offset), limit: String(query.limit) });
  if (query.q.trim()) params.set("q", query.q.trim());
  if (query.at.trim()) params.set("at", query.at.trim());
  return useQuery({
    queryKey: [...keys.unit(id), "cues", query] as const,
    queryFn: ({ signal }) => api<CuesPage>(`/api/units/${id}/cues?${params}`, { signal }),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useCorrections(id: number, lang: string) {
  return useQuery({
    queryKey: [...keys.unit(id), "corrections", lang] as const,
    queryFn: ({ signal }) => api<{ items: Correction[] }>(`/api/units/${id}/corrections?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled: lang !== "",
  });
}

/** What a change to one unit makes out of date: its own pages, and the labels and versions of the lists. */
function useRefreshUnit(id: number) {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.unit(id) }),
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
    ]);
}

/** Save a cue by hand: a new version is written at once (no model call) and the cue is locked. */
export function useEditCue(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (edit: { lang: string; cue: number; text: string }) =>
      api<{ version: number; warnings?: string[] }>(`/api/units/${id}/corrections`, {
        method: "POST",
        body: { lang: edit.lang, kind: "cue_edit", scope: "episode", payload: { cue: edit.cue, text: edit.text } },
      }),
    onSettled: () => void refresh(),
  });
}

export type CorrectionScope = "episode" | "forward" | "series";

export interface NewCorrection {
  lang: string;
  kind: "term" | "address" | "note" | "cue_edit";
  scope: CorrectionScope;
  payload: Record<string, unknown>;
}

export interface CorrectionAnswer {
  ok: boolean;
  scope?: CorrectionScope;
  ids?: number[];
  version?: number;
  warnings?: string[];
  /** For a correction that writes the glossary: how many translations it flags stale. Nothing is retranslated. */
  impact?: { stale_now: number; stale_after: number; would_stale: number };
}

/** Add a correction. It costs nothing now: it acts on the next retranslation (a cue edit writes a version at once). */
export function useAddCorrection(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (correction: NewCorrection) =>
      api<CorrectionAnswer>(`/api/units/${id}/corrections`, { method: "POST", body: correction }),
    onSettled: () => void refresh(),
  });
}

/** Take a correction off; for a cue edit that unlocks the cue and leaves the file as it is. */
export function useRemoveCorrection(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (correctionId: number) =>
      api<unknown>(`/api/units/${id}/corrections/${correctionId}/remove`, { method: "POST", body: {} }),
    onSettled: () => void refresh(),
  });
}

/** Delete the target file; the version stays in History and can be restored. */
export function useRemoveOutput(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (lang: string) => api<{ ok: boolean }>(`/api/units/${id}/output/remove`, { method: "POST", body: { lang } }),
    onSettled: () => void refresh(),
  });
}

/** Auto-request for one series saves the moment it is touched (UX §2). */
export function useSetAutoRequest(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (auto_request: "" | "on" | "off") =>
      api<unknown>(`/api/catalog/series/${id}/policy`, { method: "PUT", body: { auto_request } }),
    onSettled: () => client.invalidateQueries({ queryKey: ["catalog"] }),
  });
}

/** Ignore or stop ignoring: a decision, not a request, so it costs nothing and needs no estimate. */
export function useIgnore() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: ({ ignore, ...selector }: { ignore: boolean } & JobsSelector) =>
      api<{ ok: boolean; changed: number }>(ignore ? "/api/catalog/ignore" : "/api/catalog/unignore", {
        method: "POST",
        body: selector,
      }),
    onSettled: () => void refresh(),
  });
}
