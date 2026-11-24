import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../client";
import { apiGet } from "../route";
import type { AnswerOf } from "../types";
import { keys } from "./keys";
import { useRefreshUnit } from "./refresh";

export function useCorrections(id: number, lang: string) {
  return useQuery({
    queryKey: [...keys.unit(id), "corrections", lang] as const,
    queryFn: ({ signal }) => apiGet("/api/units/{p1}/corrections", { path: { p1: id }, query: { lang } }, signal),
    enabled: lang !== "",
  });
}

/** Save a cue by hand: a new version is written at once (no model call) and the cue is locked. */
export function useEditCue(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (edit: { lang: string; cue: number; text: string }) =>
      api<AnswerOf<"/api/units/{p1}/corrections", "post">>(`/api/units/${id}/corrections`, {
        method: "POST",
        body: { lang: edit.lang, kind: "cue_edit", scope: "episode", payload: { cue: edit.cue, text: edit.text } },
      }),
    onSettled: () => void refresh(),
  });
}

export type CorrectionScope = "episode" | "forward" | "series";

export interface NewCorrection {
  lang: string;
  kind: "term" | "address" | "note" | "cue_edit";
  scope: CorrectionScope;
  payload: Record<string, unknown>;
}

/** Add a correction. It costs nothing now: it acts on the next retranslation (a cue edit writes a version at once). */
export function useAddCorrection(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (correction: NewCorrection) =>
      api<AnswerOf<"/api/units/{p1}/corrections", "post">>(`/api/units/${id}/corrections`, { method: "POST", body: correction }),
    onSettled: () => void refresh(),
  });
}

/** Take a correction off; for a cue edit that unlocks the cue and leaves the file as it is. */
export function useRemoveCorrection(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (correctionId: number) =>
      api<AnswerOf<"/api/units/{p1}/corrections/{p2}/remove", "post">>(`/api/units/${id}/corrections/${correctionId}/remove`, { method: "POST", body: {} }),
    onSettled: () => void refresh(),
  });
}

/** Delete the target file; the version stays in History and can be restored. */
export function useRemoveOutput(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (lang: string) => api<AnswerOf<"/api/units/{p1}/output/remove", "post">>(`/api/units/${id}/output/remove`, { method: "POST", body: { lang } }),
    onSettled: () => void refresh(),
  });
}
