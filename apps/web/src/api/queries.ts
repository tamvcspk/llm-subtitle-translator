import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AuthMe, CatalogOverview, CatalogStatus, JobsAnswer, JobsSelector, MoviesPage, StatusResponse } from "./types";

export const keys = {
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

/** What a request would cost (UX §6.4): nothing is made, saved or called. */
export function useEstimate(selector: JobsSelector | null) {
  return useQuery({
    queryKey: ["estimate", selector],
    queryFn: ({ signal }) =>
      api<JobsAnswer>("/api/jobs", { method: "POST", body: { ...selector, estimate_only: true }, signal }),
    enabled: selector !== null,
    // A price is only true now: never reuse the one from the last time the box was open.
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** Confirm: make the jobs, then every number that counts them is out of date. */
export function useRequestJobs() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (selector: JobsSelector) => api<JobsAnswer>("/api/jobs", { method: "POST", body: selector }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["catalog"] });
      void client.invalidateQueries({ queryKey: keys.status });
    },
  });
}
