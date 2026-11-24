import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { readsOnly } from "../../lib/activity";
import { api } from "../client";
import type { AnswerOf, QueueAnswer, QueueJob } from "../types";
import { keys } from "./keys";

export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: ({ signal }) => api<AnswerOf<"/api/status", "get">>("/api/status", { signal }),
    refetchInterval: 15_000,
  });
}

/** The Activity page: the latest 100 jobs of each state, the real counts and the health of each model. */
export function useQueue() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.queue,
    queryFn: ({ signal }) => api<AnswerOf<"/api/queue", "get">>("/api/queue", { signal }),
    refetchInterval: 5_000,
  });
  // A job that reads (the facts of an episode, proposals for a series) stores its result where other pages look: when one
  // that was open is not any more, those pages read again.
  const reading = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!query.data) return;
    const now = new Set(openReadingJobs(query.data).map((job) => job.job_id));
    const finished = [...reading.current].some((id) => !now.has(id));
    reading.current = now;
    if (finished) {
      for (const queryKey of [["memory"], ["unit-facts"], ["suggestions"], ["research-estimate"], ["library"], ["unit"]]) {
        void client.invalidateQueries({ queryKey });
      }
    }
  }, [query.data, client]);
  return query;
}

/** The jobs that read and do not translate (`extract`, `suggest`, `research`, `judge`) that are still open: running, in line or waiting. */
function openReadingJobs(queue: QueueAnswer): QueueJob[] {
  return [...queue.processing, ...queue.pending, ...queue.deferred].filter((job) => readsOnly(job));
}

/** The bundle of a job's latest attempt, read only when its box is open (it holds every cue, so it can be large). */
export function useJobSpec(jobId: string | null) {
  return useQuery({
    queryKey: ["job-spec", jobId] as const,
    queryFn: ({ signal }) => api<unknown>(`/api/jobs/${encodeURIComponent(jobId ?? "")}/spec`, { signal }),
    enabled: jobId !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: ({ signal }) => api<AnswerOf<"/api/auth/me", "get">>("/api/auth/me", { signal }),
    staleTime: Infinity,
  });
}

export function useSignOut() {
  return useMutation({
    mutationFn: () => api<AnswerOf<"/api/auth/logout", "post">>("/api/auth/logout", { method: "POST", body: {} }),
    onSuccess: () => window.location.assign("/login"),
  });
}
