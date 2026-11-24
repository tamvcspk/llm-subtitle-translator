import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import type { AnswerOf, MemoryPolicy, QueueAnswer, QueueJob } from "../types";
import { useRefreshJobs } from "./refresh";
import { useQueue } from "./core";

export function useSeriesMemory(storedId: number | null | undefined) {
  return useQuery({
    queryKey: ["memory", storedId ?? 0] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/series/{p1}/memory", "get">>(`/api/series/${storedId}/memory`, { signal }),
    enabled: typeof storedId === "number",
    refetchInterval: 30_000,
  });
}

/**
 * Opening the Context tab: the worker gives the catalog's episodes the units the tab lists. It is a POST because reading
 * the memory of a series (polled every 30 s) never writes; the list is read again only when something was made.
 */
export function useAdoptEpisodes(storedId: number | null | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<AnswerOf<"/api/series/{p1}/memory/adopt", "post">>(`/api/series/${storedId}/memory/adopt`, { method: "POST", body: {} }),
    onSuccess: (result) => {
      if (result.adopted > 0) void client.invalidateQueries({ queryKey: ["memory"] });
    },
  });
}

/** Everything a change of story memory can make stale: the labels and versions of the lists, and every episode's context. */
function useRefreshMemory() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["memory"] }),
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
      client.invalidateQueries({ queryKey: ["unit"] }),
    ]);
}

/** **Apply**: the series' switch and where its specials sit. Translations made without it become Stale; nothing is redone. */
export function useSaveMemory(storedId: number) {
  const refresh = useRefreshMemory();
  return useMutation({
    mutationFn: (body: { policy: MemoryPolicy; specials_after: string }) =>
      api<AnswerOf<"/api/series/{p1}/memory", "put">>(`/api/series/${storedId}/memory`, { method: "PUT", body }),
    onSettled: () => void refresh(),
  });
}

/**
 * Read the facts of these episodes (one model call each): the worker makes one job per episode and the queue runs them
 * under the day's quota like a translation. `anchor` is any episode of the series (the request is made through it).
 */
export function useExtractFacts() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: (request: { anchor: number; unitIds: number[] }) =>
      api<AnswerOf<"/api/units/{p1}/context/extract", "post">>(`/api/units/${request.anchor}/context/extract`, { method: "POST", body: { unit_ids: request.unitIds } }),
    onSettled: () => void refresh(),
  });
}

/** An `extract` job still open for an episode: running, in line or waiting (for the quota, say). */
export type OpenExtract = QueueJob & { running: boolean };

export function openExtractJobs(queue: QueueAnswer | undefined, unitIds: ReadonlySet<number>): OpenExtract[] {
  if (!queue) return [];
  const mine = (job: QueueJob) => job.kind === "extract" && typeof job.unit_id === "number" && unitIds.has(job.unit_id);
  return [
    ...queue.processing.filter(mine).map((job) => ({ ...job, running: true })),
    ...[...queue.pending, ...queue.deferred].filter(mine).map((job) => ({ ...job, running: false })),
  ];
}

/** The reading of these episodes as the queue shows it (the queue's own polling reads the facts again when a job ends). */
export function useExtractJobs(unitIds: ReadonlySet<number>) {
  return openExtractJobs(useQueue().data, unitIds);
}

/** Stop several jobs at once: the ones in line are cancelled, a running one stops before its next step. */
export function useStopJobs() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: (jobIds: string[]) =>
      Promise.all(jobIds.map((jobId) => api<AnswerOf<"/api/queue/stop", "post">>("/api/queue/stop", { method: "POST", body: { job_id: jobId } }))),
    onSettled: () => void refresh(),
  });
}

/** What the stored facts of an episode say, read when its row is opened. */
export function useUnitFacts(unitId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["unit-facts", unitId] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/units/{p1}/facts", "get">>(`/api/units/${unitId}/facts`, { signal }),
    enabled,
  });
}
