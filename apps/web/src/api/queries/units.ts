import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import { apiGet } from "../route";
import type { AdoptAnswer, AnswerOf } from "../types";
import { keys } from "./keys";
import { useRefreshUnit } from "./refresh";

/** What adopting a variant with its style would flag stale in the other episodes (`dry_run`): the counts only. */
type AdoptImpact = Required<Pick<AdoptAnswer, "stale_now" | "stale_after" | "would_stale">>;

export function useUnit(id: number) {
  return useQuery({
    queryKey: keys.unit(id),
    queryFn: ({ signal }) => api<AnswerOf<"/api/units/{p1}", "get">>(`/api/units/${id}`, { signal }),
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
  const q = query.q.trim();
  const at = query.at.trim();
  return useQuery({
    queryKey: [...keys.unit(id), "cues", query] as const,
    queryFn: ({ signal }) =>
      apiGet(
        "/api/units/{p1}/cues",
        { path: { p1: id }, query: { lang: query.lang, offset: query.offset, limit: query.limit, q: q || undefined, at: at || undefined } },
        signal,
      ),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useUnitContext(id: number, lang: string) {
  return useQuery({
    queryKey: [...keys.unit(id), "context", lang] as const,
    queryFn: ({ signal }) => apiGet("/api/units/{p1}/context", { path: { p1: id }, query: { lang } }, signal),
    enabled: lang !== "",
  });
}

export function useVersions(id: number, lang: string) {
  return useQuery({
    queryKey: [...keys.unit(id), "versions", lang] as const,
    queryFn: ({ signal }) => apiGet("/api/units/{p1}/versions", { path: { p1: id }, query: { lang } }, signal),
    enabled: lang !== "",
  });
}

/** What changed from `against` (default: the version before) to `version`. Read when asked for, never before. */
export function useDiff(id: number, lang: string, version: number | null) {
  return useQuery({
    queryKey: [...keys.unit(id), "diff", lang, version] as const,
    queryFn: ({ signal }) =>
      apiGet("/api/units/{p1}/versions/{p2}/diff", { path: { p1: id, p2: version! }, query: { lang } }, signal),
    enabled: version !== null,
  });
}

export function useCompare(id: number, lang: string, version: number) {
  return useQuery({
    queryKey: [...keys.unit(id), "compare", lang, version] as const,
    queryFn: ({ signal }) =>
      apiGet("/api/units/{p1}/versions/{p2}/compare", { path: { p1: id, p2: version }, query: { lang } }, signal),
    enabled: lang !== "",
    // While a judge job is open its verdicts arrive batch by batch: look again every few seconds, and stop when it ends.
    // A job parked on the day's quota can wait for hours and changes nothing meanwhile: look only once a minute.
    refetchInterval: (query) => {
      const job = query.state.data?.judge?.job;
      return job ? (job.state === "waiting" ? 60_000 : 4000) : false;
    },
  });
}

/** What keeping a variant's style as the series' own would flag stale: a dry run of "Use this version" (UX §4). */
export function useAdoptImpact(id: number, lang: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: ["adopt-impact", id, lang, version] as const,
    queryFn: ({ signal }) =>
      api<AdoptImpact>(`/api/units/${id}/versions/${version}/adopt`, {
        method: "POST",
        body: { lang, apply_style: true, dry_run: true },
        signal,
      }),
    enabled,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

export type Winner = "variant" | "current" | "tie";

/**
 * The admin's verdict on a candidate. It is recorded as a quality signal; with `apply` it is acted on: the cues won by
 * the variant become cue edits, and a win of the whole episode adopts the candidate (and, with `apply_style`, its style).
 */
export function useChoose(id: number, version: number) {
  const refresh = useRefreshUnit(id);
  const client = useQueryClient();
  return useMutation({
    mutationFn: (verdict: { lang: string; picks?: Record<string, Winner>; whole?: Winner; apply: boolean; apply_style?: boolean }) =>
      api<AnswerOf<"/api/units/{p1}/versions/{p2}/choose", "post">>(`/api/units/${id}/versions/${version}/choose`, {
        method: "POST",
        body: verdict,
      }),
    onSettled: () => {
      void refresh();
      void client.invalidateQueries({ queryKey: ["style"] });
    },
  });
}

/** Restore an older version: it is written back as a new one, and what was on disk is kept first. No model call. */
export function useRevert(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (args: { version: number; lang: string }) =>
      api<AnswerOf<"/api/units/{p1}/versions/{p2}/revert", "post">>(`/api/units/${id}/versions/${args.version}/revert`, {
        method: "POST",
        body: { lang: args.lang },
      }),
    onSettled: () => void refresh(),
  });
}

/** "Use this version": a candidate (a result that was not allowed onto the disk) is written and becomes current. */
export function useAdopt(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (args: { version: number; lang: string }) =>
      api<AnswerOf<"/api/units/{p1}/versions/{p2}/adopt", "post">>(`/api/units/${id}/versions/${args.version}/adopt`, {
        method: "POST",
        body: { lang: args.lang },
      }),
    onSettled: () => void refresh(),
  });
}

/** How many cues the target has: one cheap read of the cue list, for a line that says what a range would overwrite. */
export function useCueTotal(id: number, lang: string) {
  return useCues(id, { lang, q: "", offset: 0, at: "", limit: 1 }, lang !== "");
}
