import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import { apiGet } from "../route";
import type { AnswerOf } from "../types";

/** The figures of FR-19.5 for the last `days` days, worked out by the worker from its own records. */
export function useInsights(days: number) {
  return useQuery({
    queryKey: ["insights", days] as const,
    queryFn: ({ signal }) => apiGet("/api/metrics", { query: { days } }, signal),
    refetchInterval: 30_000,
  });
}

/** The series the worker keeps, for the golden sets. */
export function useLibrarySeries() {
  return useQuery({
    queryKey: ["library", "series"] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/library/series", "get">>("/api/library/series", { signal }),
  });
}

/** The golden set of one series and language as data, for the Copy button of its sheet (the file itself is a link, not this). */
export function fetchGolden(seriesId: number, lang: string, signal?: AbortSignal) {
  return apiGet("/api/series/{series_id}/golden", { path: { series_id: seriesId }, query: { lang } }, signal);
}

/** Look at the corrections of one series for a word the admin keeps changing. Free: no model is asked. */
export function useFromEdits() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { seriesId: number; lang: string }) =>
      api<AnswerOf<"/api/suggestions/from-edits", "post">>("/api/suggestions/from-edits", { method: "POST", body: { series_id: args.seriesId, lang: args.lang } }),
    onSettled: () => client.invalidateQueries({ queryKey: ["suggestions"] }),
  });
}
