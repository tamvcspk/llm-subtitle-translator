import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AuthMe,
  CatalogOverview,
  CatalogStatus,
  JobsAnswer,
  JobsSelector,
  LibraryUnits,
  MoviesPage,
  SeriesPage,
  StatusResponse,
} from "./types";

export const keys = {
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
