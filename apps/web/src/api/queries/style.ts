import { useMutation, useQuery } from "@tanstack/react-query";
import type { StyleDraft } from "../../lib/style";
import { api } from "../client";
import { ownerUrl } from "../route";
import type { AnswerOf, Style, StyleImpact, StyleOverview, StylePreview } from "../types";
import { useRefreshStyle } from "./refresh";

/** Whose style it is: a series, or a movie (reached by the movie's unit id). */
export type StyleOwner = { kind: "series" | "movie"; id: number };

export function useOwnerStyle(owner: StyleOwner | null, lang: string) {
  return useQuery({
    queryKey: ["style", owner, lang] as const,
    queryFn: ({ signal }) => api<StyleOverview>(ownerUrl(owner!, "style", { lang }), { signal }),
    enabled: owner !== null && lang !== "",
  });
}

/** Presets that fit the series' genres on TMDB (it asks TMDB, so once in a while is enough). */
export function useStyleSuggestions(seriesId: number | null) {
  return useQuery({
    queryKey: ["style", "suggestions", seriesId] as const,
    queryFn: ({ signal }) =>
      api<AnswerOf<"/api/series/{p1}/style/suggestions", "get">>(`/api/series/${seriesId}/style/suggestions`, { signal }),
    enabled: seriesId !== null,
    staleTime: 10 * 60_000,
    retry: false,
  });
}

/** What saving this draft would flag stale: a dry run that counts, stores nothing and costs no model call (UX §6.2). */
export function useStyleImpact(owner: StyleOwner | { kind: "episode"; id: number }, lang: string, draft: StyleDraft, enabled: boolean) {
  return useQuery({
    queryKey: ["style-impact", owner, lang, draft] as const,
    queryFn: ({ signal }) =>
      owner.kind === "episode"
        ? api<AnswerOf<"/api/units/{p1}/style", "put">>(`/api/units/${owner.id}/style`, { method: "PUT", body: { lang, style: draft.style, dry_run: true }, signal })
        : api<StyleImpact>(ownerUrl<"style", "put">(owner, "style", { lang }), {
            method: "PUT",
            body: { preset: draft.preset, style: draft.style, dry_run: true },
            signal,
          }),
    enabled,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}


export function useSaveStyle(owner: StyleOwner | { kind: "episode"; id: number }, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (draft: StyleDraft) =>
      owner.kind === "episode"
        ? api<AnswerOf<"/api/units/{p1}/style", "put">>(`/api/units/${owner.id}/style`, { method: "PUT", body: { lang, style: draft.style } })
        : api<StyleImpact>(ownerUrl<"style", "put">(owner, "style", { lang }), {
            method: "PUT",
            body: { preset: draft.preset, style: draft.style },
          }),
    onSettled: () => void refresh(),
  });
}

/** The override of one episode: the layer above its series' style. */
export function useEpisodeStyle(unitId: number) {
  return useQuery({
    queryKey: ["style", "episode", unitId] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/units/{p1}/style", "get">>(`/api/units/${unitId}/style`, { signal }),
  });
}

export interface PreviewRequest {
  lang: string;
  unit_id?: number;
  cues?: number[];
  preset: string;
  style: Style;
}

/** "Try 20 lines": one model call on the draft, written nowhere. */
export function usePreviewStyle(owner: StyleOwner) {
  return useMutation({
    mutationFn: (request: PreviewRequest) => api<StylePreview>(ownerUrl<"style/preview", "post">(owner, "style/preview"), { method: "POST", body: request }),
  });
}
