import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import { apiGet } from "../route";
import type { AnswerOf, Suggestion, SuggestionList, SuggestionPayload, SuggestionStatus } from "../types";

/** How many suggestions wait, for the badge on the nav. Cheap on purpose: it is asked on every page. */
export function useSuggestionSummary() {
  return useQuery({
    queryKey: ["suggestions", "summary"] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/suggestions/summary", "get">>("/api/suggestions/summary", { signal }),
    refetchInterval: 30_000,
    retry: false,
  });
}

/** The pending ones, or the decided ones (accepted and rejected, newest first) for the History tab. */
export function useSuggestions(status: SuggestionStatus | "decided", enabled = true) {
  return useQuery({
    queryKey: ["suggestions", "list", status] as const,
    enabled,
    queryFn: async ({ signal }): Promise<SuggestionList> => {
      if (status !== "decided") return apiGet("/api/suggestions", { query: { status } }, signal);
      const [accepted, rejected] = await Promise.all([
        apiGet("/api/suggestions", { query: { status: "accepted" } }, signal),
        apiGet("/api/suggestions", { query: { status: "rejected" } }, signal),
      ]);
      const items: Suggestion[] = [...accepted.items, ...rejected.items].sort((a, b) => b.decided_at - a.decided_at);
      return { ...accepted, items };
    },
    refetchInterval: 30_000,
  });
}

/** Google's Search Suggestions of a research search: unchanged HTML, fetched only for the card that shows it. */
export function useSearchSuggestions(id: number | null | undefined) {
  return useQuery({
    queryKey: ["suggestions", "search", id] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/suggestions/search/{p1}", "get">>(`/api/suggestions/search/${id}`, { signal }),
    enabled: typeof id === "number",
    staleTime: Infinity,
    retry: false,
  });
}

/** What accepting this suggestion (as edited, when `payload` is given) would flag stale: a dry run, nothing is written (FR-11.1.3). */
export function useSuggestionImpact(id: number, payload: SuggestionPayload | null, enabled: boolean) {
  return useQuery({
    queryKey: ["suggestion-impact", id, payload] as const,
    queryFn: ({ signal }) =>
      api<AnswerOf<"/api/suggestions/{p1}/accept", "post">>(`/api/suggestions/${id}/accept`, {
        method: "POST",
        body: { dry_run: true, ...(payload ? { payload } : {}) },
        signal,
      }),
    enabled,
    staleTime: 20_000,
    gcTime: 20_000,
    retry: false,
  });
}

/** What accepting every pending suggestion of one series together would flag stale. */
export function useBulkImpact(seriesId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["suggestion-bulk-impact", seriesId] as const,
    queryFn: ({ signal }) =>
      api<AnswerOf<"/api/suggestions/bulk", "post">>("/api/suggestions/bulk", { method: "POST", body: { series_id: seriesId, dry_run: true }, signal }),
    enabled,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/**
 * A decision changes the badge, the lists, and (an accept or an undo) the glossary and every label that says Stale.
 * `decided` names what was just accepted or rejected, and only when that succeeded: its card is about to leave the inbox
 * and the worker refuses a dry run of a decided suggestion (400), so its impact is not asked again. `"all"` (a bulk
 * accept) first lets the list drop every card it decided, then refreshes the impact of the ones still showing. After a
 * failure nothing is left out: the card stays and its count is looked at again.
 */
function useRefreshSuggestions() {
  const client = useQueryClient();
  return async (decided?: number | "all") => {
    if (decided === "all") await client.invalidateQueries({ queryKey: ["suggestions"] });
    await Promise.all([
      decided === "all" ? undefined : client.invalidateQueries({ queryKey: ["suggestions"] }),
      client.invalidateQueries({
        queryKey: ["suggestion-impact"],
        predicate: (query) => typeof decided !== "number" || query.queryKey[1] !== decided,
      }),
      client.invalidateQueries({ queryKey: ["suggestion-bulk-impact"] }),
      client.invalidateQueries({ queryKey: ["research-estimate"] }),
      client.invalidateQueries({ queryKey: ["glossary"] }),
      client.invalidateQueries({ queryKey: ["style"] }),
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
      client.invalidateQueries({ queryKey: ["unit"] }),
    ]);
  };
}

/** Accept, or edit then accept (`payload`). The glossary changes; translations made without it are flagged stale, never redone. */
export function useAcceptSuggestion() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (args: { id: number; payload?: SuggestionPayload }) =>
      api<AnswerOf<"/api/suggestions/{p1}/accept", "post">>(`/api/suggestions/${args.id}/accept`, { method: "POST", body: args.payload ? { payload: args.payload } : {} }),
    onSettled: (_answer, error, args) => void refresh(error ? undefined : args.id),
  });
}

/** Reject: the same content is never proposed again. */
export function useRejectSuggestion() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (id: number) => api<AnswerOf<"/api/suggestions/{p1}/reject", "post">>(`/api/suggestions/${id}/reject`, { method: "POST", body: {} }),
    onSettled: (_answer, error, id) => void refresh(error ? undefined : id),
  });
}

/** Put back what an accept replaced; the suggestion returns to the inbox. */
export function useUndoSuggestion() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (id: number) => api<AnswerOf<"/api/suggestions/{p1}/undo", "post">>(`/api/suggestions/${id}/undo`, { method: "POST", body: {} }),
    onSettled: () => void refresh(),
  });
}

/** Accept every pending suggestion of one series; `expect` is the number the admin confirmed. */
export function useBulkAccept() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (args: { seriesId: number; expect: number }) =>
      api<AnswerOf<"/api/suggestions/bulk", "post">>("/api/suggestions/bulk", { method: "POST", body: { series_id: args.seriesId, expect: args.expect } }),
    onSettled: (_answer, error) => void refresh(error ? undefined : "all"),
  });
}
