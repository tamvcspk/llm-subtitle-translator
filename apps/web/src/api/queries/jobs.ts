import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import type { AnswerOf, Estimate, EstimateAnswer, JobsSelector, PrerequisitesRequested, Style } from "../types";
import { keys } from "./keys";
import { useRefreshJobs } from "./refresh";

/** What a route that can say a price answers with `estimate_only` (a retranslation, a variant, the judge). */
type PriceAnswer = { estimate: Estimate };

/** A button that spends model calls says what it is about: translate these, or retry that failed job. */
export type CostRequest =
  | { selector: JobsSelector }
  | { retry: { job_id: string } | { all: true } }
  | { prerequisites: { job_id: string } }
  | { retranslate: { unit_id: number; body: RetranslateBody } }
  | { variant: { unit_id: number; body: VariantBody } }
  | { judge: { unit_id: number; version: number; body: JudgeBody } };

/** The judge reads a candidate next to the current translation; `again` also asks about the cues it has already judged. */
export interface JudgeBody {
  lang: string;
  again?: boolean;
}

/** The whole episode translated with another style, kept as a candidate to compare with the current one. */
export interface VariantBody {
  lang: string;
  label: string;
  preset: string;
  style: Style;
}

/** `range` is `{cues: [first, last]}` or `{time: [start, end]}`; leave it out for the whole episode. */
export interface RetranslateBody {
  lang: string;
  range?: { cues: [number, number] } | { time: [string, string] };
  /** The term, address and note corrections to use; leave out for all of them. */
  correction_ids?: number[];
}

/** What a request would cost (UX §6.4): nothing is made, saved or called. */
export function useEstimate(request: CostRequest | null) {
  return useQuery({
    queryKey: ["estimate", request],
    queryFn: async ({ signal }): Promise<EstimateAnswer> => {
      if (request && "retry" in request) {
        return api<EstimateAnswer>("/api/queue/retry", { method: "POST", body: { ...request.retry, estimate_only: true }, signal });
      }
      if (request && "prerequisites" in request) {
        return api<EstimateAnswer>("/api/queue/request-prerequisites", {
          method: "POST",
          body: { ...request.prerequisites, estimate_only: true },
          signal,
        });
      }
      if (request && ("retranslate" in request || "variant" in request)) {
        const [path, call] = "retranslate" in request ? ["retranslate", request.retranslate] : ["variant", request.variant];
        const answer = await api<PriceAnswer>(`/api/units/${call.unit_id}/${path}`, {
          method: "POST",
          body: { ...call.body, estimate_only: true },
          signal,
        });
        return { estimate_only: true, estimate: answer.estimate, planned_total: answer.estimate.jobs, skipped_total: 0, skipped_by_reason: {} };
      }
      if (request && "judge" in request) {
        const { unit_id, version, body } = request.judge;
        const answer = await api<PriceAnswer>(`/api/units/${unit_id}/versions/${version}/judge`, {
          method: "POST",
          body: { ...body, estimate_only: true },
          signal,
        });
        return { estimate_only: true, estimate: answer.estimate, planned_total: answer.estimate.jobs, skipped_total: 0, skipped_by_reason: {} };
      }
      return api<AnswerOf<"/api/jobs", "post">>("/api/jobs", { method: "POST", body: { ...request?.selector, estimate_only: true }, signal });
    },
    enabled: request !== null,
    // A price is only true now: never reuse the one from the last time the box was open.
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** Confirm: make the jobs (or put the failed one back in line); `count` is how many are now in line. */
export function useRequestJobs() {
  const refresh = useRefreshJobs();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (request: CostRequest): Promise<{ count: number }> => {
      if ("retry" in request) {
        const answer = await api<{ ok: boolean; count?: number }>("/api/queue/retry", { method: "POST", body: request.retry });
        if (!answer.ok) throw new Error("Nothing could be retried: the jobs are not failed any more, or their files already have a job.");
        return { count: answer.count ?? 1 };
      }
      if ("prerequisites" in request) {
        const answer = await api<PrerequisitesRequested>("/api/queue/request-prerequisites", { method: "POST", body: request.prerequisites });
        return { count: answer.requested.length };
      }
      if ("retranslate" in request || "variant" in request) {
        const [path, call] = "retranslate" in request ? ["retranslate", request.retranslate] : ["variant", request.variant];
        await api<{ ok: boolean }>(`/api/units/${call.unit_id}/${path}`, { method: "POST", body: call.body });
        return { count: 1 };
      }
      if ("judge" in request) {
        const { unit_id, version, body } = request.judge;
        await api<AnswerOf<"/api/units/{p1}/versions/{p2}/judge", "post">>(`/api/units/${unit_id}/versions/${version}/judge`, { method: "POST", body });
        void client.invalidateQueries({ queryKey: keys.unit(unit_id) }); // the comparison now says a judge is at work
        return { count: 1 };
      }
      const answer = await api<AnswerOf<"/api/jobs", "post">>("/api/jobs", { method: "POST", body: request.selector });
      return { count: answer.count ?? 0 };
    },
    onSettled: () => void refresh(),
  });
}

/** Cancel a requested or waiting job, or ask a running one to stop at the next batch (no model call, no estimate). */
export function useStopJob() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: (jobId: string) => api<AnswerOf<"/api/queue/stop", "post">>("/api/queue/stop", { method: "POST", body: { job_id: jobId } }),
    onSettled: () => void refresh(),
  });
}
