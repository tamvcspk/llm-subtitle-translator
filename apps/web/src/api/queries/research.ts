import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import type { AnswerOf, ResearchEstimate } from "../types";
import { useRefreshJobs } from "./refresh";

/**
 * What a run of Research terms would look up and spend (UX §6.7): the `N` of the button, why it is off, the price. It asks
 * the worker (`estimate_only`), which counts from the stored facts and calls no model. Never reused: a price is only true now.
 */
export function useResearchEstimate(seriesId: number | null, lang: string) {
  return useQuery({
    queryKey: ["research-estimate", seriesId, lang] as const,
    queryFn: ({ signal }) =>
      api<ResearchEstimate>("/api/suggestions/research", { method: "POST", body: { series_id: seriesId, lang, estimate_only: true }, signal }),
    enabled: seriesId !== null && lang !== "",
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** **Confirm** in the Research box: queue the job. `autoAccept` is the tick of the box, for this run only. */
export function useResearchTerms() {
  const refresh = useRefreshJobs();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { seriesId: number; lang: string; autoAccept: boolean }) =>
      api<AnswerOf<"/api/suggestions/research", "post">>("/api/suggestions/research", {
        method: "POST",
        body: { series_id: args.seriesId, lang: args.lang, auto_accept: args.autoAccept },
      }),
    onSettled: () => {
      void refresh();
      void client.invalidateQueries({ queryKey: ["research-estimate"] });
    },
  });
}
