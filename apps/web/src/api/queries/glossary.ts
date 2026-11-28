import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../client";
import { ownerUrl } from "../route";
import type { AnswerOf, CharacterCard, GlossaryAnswer, GlossaryChange, GlossaryDoc, GlossaryHistory, GlossarySave, StyleImpact } from "../types";
import { useRefreshStyle } from "./refresh";
import type { StyleOwner } from "./style";

export function useGlossary(owner: StyleOwner | null, lang: string) {
  return useQuery({
    queryKey: ["glossary", owner, lang] as const,
    queryFn: ({ signal }) => api<GlossaryAnswer>(ownerUrl(owner!, "glossary", { lang }), { signal }),
    enabled: owner !== null && lang !== "",
  });
}

/** A glossary as the form holds it, or as YAML text pasted into Import. */
export type GlossaryBody = { doc: GlossaryDoc } | { yaml: string };

/** What saving this glossary would flag stale: a dry run that counts, stores nothing and costs no model call (UX §6.2). A glossary the worker refuses is an error that names the path. */
export function useGlossaryDryRun(owner: StyleOwner, lang: string, body: GlossaryBody | null, enabled: boolean) {
  return useQuery({
    queryKey: ["glossary-impact", owner, lang, body] as const,
    queryFn: ({ signal }) =>
      api<GlossarySave>(ownerUrl<"glossary", "put">(owner, "glossary", { lang }), {
        method: "PUT",
        body: { ...body, dry_run: true },
        signal,
      }),
    enabled: enabled && body !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

export function useSaveGlossary(owner: StyleOwner, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (body: GlossaryBody) =>
      api<GlossarySave>(ownerUrl<"glossary", "put">(owner, "glossary", { lang }), { method: "PUT", body }),
    onSettled: () => void refresh(),
  });
}

export function useGlossaryHistory(owner: StyleOwner, lang: string, enabled: boolean) {
  return useQuery({
    queryKey: ["glossary", owner, lang, "history"] as const,
    queryFn: ({ signal }) =>
      api<GlossaryHistory>(ownerUrl(owner, "glossary/history", { lang }), { signal }),
    enabled,
    gcTime: 0,
  });
}

/** What restoring a version would flag stale: a dry run that counts, restores nothing and costs no model call. */
export function useRestoreImpact(owner: StyleOwner, lang: string, version: number | null) {
  return useQuery({
    queryKey: ["glossary-restore-impact", owner, lang, version] as const,
    queryFn: ({ signal }) =>
      api<GlossaryChange>(ownerUrl<"glossary/restore", "post">(owner, "glossary/restore"), { method: "POST", body: { lang, version, dry_run: true }, signal }),
    enabled: version !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** Put an earlier state back as a new save; the one it replaces goes to the history, so this can be undone. */
export function useRestoreGlossary(owner: StyleOwner, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (version: number) =>
      api<GlossaryChange & { restored: number }>(ownerUrl<"glossary/restore", "post">(owner, "glossary/restore"), { method: "POST", body: { lang, version } }),
    onSettled: () => void refresh(),
  });
}

export interface CopyArgs {
  from_series: number;
  merge: boolean;
}

/** What copying another series' glossary would flag stale: a dry run that counts, copies nothing and costs no model call. */
export function useCopyImpact(seriesId: number, lang: string, args: CopyArgs | null) {
  return useQuery({
    queryKey: ["glossary-copy-impact", seriesId, lang, args] as const,
    queryFn: ({ signal }) =>
      api<AnswerOf<"/api/series/{p1}/glossary/copy", "post">>(`/api/series/${seriesId}/glossary/copy`, {
        method: "POST",
        body: { lang, ...args, dry_run: true },
        signal,
      }),
    enabled: args !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** Copy another series' glossary into this one: a copy, not a link (series only; a movie has no copy). */
export function useCopyGlossary(seriesId: number, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (args: CopyArgs) =>
      api<AnswerOf<"/api/series/{p1}/glossary/copy", "post">>(`/api/series/${seriesId}/glossary/copy`, { method: "POST", body: { lang, ...args } }),
    onSettled: () => void refresh(),
  });
}

/** What saving a card would flag stale; `replaces` is the name it had when it is renamed. */
export function useCardImpact(owner: StyleOwner, lang: string, request: { card: CharacterCard; replaces?: string } | null, enabled: boolean) {
  return useQuery({
    queryKey: ["card-impact", owner, lang, request] as const,
    queryFn: ({ signal }) =>
      api<StyleImpact>(ownerUrl<"cards", "put">(owner, "cards", { lang }), {
        method: "PUT",
        body: { ...request, dry_run: true },
        signal,
      }),
    enabled: enabled && request !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/**
 * What saving a character would flag stale, as one count over its Name and its Voice together: an episode both reach is
 * counted once (the worker counts them in one pass, `dry_run` of the same route that saves, P19b). `delete_card` is the
 * name of a card that would go (R19-7): a character with only a Voice has a number too.
 */
export function useCharacterImpact(
  owner: StyleOwner,
  lang: string,
  parts: { glossary: GlossaryDoc | null; card: { card: CharacterCard; replaces?: string } | null; delete_card?: string | null } | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["character-impact", owner, lang, parts] as const,
    queryFn: ({ signal }) =>
      api<StyleImpact>(ownerUrl<"characters", "put">(owner, "characters", { lang }), {
        method: "PUT",
        body: {
          ...(parts?.glossary ? { glossary: parts.glossary } : {}),
          ...(parts?.card ? { card: parts.card } : {}),
          ...(parts?.delete_card ? { delete_card: parts.delete_card } : {}),
          dry_run: true,
        },
        signal,
      }),
    enabled: enabled && parts !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** Save a character: its Name goes into the glossary and its Voice into its card, in one request the worker writes in one transaction (UX §6.3). */
export function useSaveCharacter(owner: StyleOwner, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (parts: { glossary: GlossaryDoc | null; card: { card: CharacterCard; replaces?: string } | null }) =>
      api<{ ok: boolean }>(ownerUrl<"characters", "put">(owner, "characters", { lang }), {
        method: "PUT",
        body: { ...(parts.glossary ? { glossary: parts.glossary } : {}), ...(parts.card ? { card: parts.card } : {}) },
      }),
    onSettled: () => void refresh(),
  });
}

/** Delete a character from both places it lives in, in one request: the card goes and the glossary is written without its Name. */
export function useDeleteCharacter(owner: StyleOwner, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (parts: { glossary: GlossaryDoc | null; card: string | null }) =>
      api<{ ok: boolean }>(ownerUrl<"characters", "put">(owner, "characters", { lang }), {
        method: "PUT",
        body: { ...(parts.glossary ? { glossary: parts.glossary } : {}), ...(parts.card !== null ? { delete_card: parts.card } : {}) },
      }),
    onSettled: () => void refresh(),
  });
}
