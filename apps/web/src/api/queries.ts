import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import type { StyleDraft } from "../lib/style";
import { api } from "./client";
import type {
  AuthMe,
  BackgroundTask,
  BulkAnswer,
  CatalogOverview,
  CatalogStatus,
  CharacterCard,
  CompareAnswer,
  Correction,
  CuesPage,
  DiffAnswer,
  Estimate,
  GlossaryAnswer,
  GlossaryDoc,
  GlossaryRevision,
  GlossaryChange,
  GlossarySave,
  JobsAnswer,
  JobsSelector,
  LibraryUnits,
  MemoryPolicy,
  MoviesPage,
  QueueAnswer,
  SeriesMemory,
  SeriesPage,
  StatusResponse,
  Style,
  StyleImpact,
  StyleOverview,
  StylePreview,
  Suggestion,
  SuggestionDecision,
  SuggestionList,
  SuggestionPayload,
  SuggestionStatus,
  SuggestionSummary,
  TaskStarted,
  UnitContextAnswer,
  UnitDetail,
  UnitFacts,
  VersionsAnswer,
} from "./types";

export const keys = {
  unit: (id: number) => ["unit", id] as const,
  series: (id: number) => ["catalog", "series", id] as const,
  seriesUnits: (storedId: number) => ["library", "series", storedId, "units"] as const,
  status: ["status"] as const,
  catalogStatus: ["catalog", "status"] as const,
  me: ["auth", "me"] as const,
  queue: ["queue"] as const,
  tasks: ["tasks"] as const,
  catalog: ["catalog", "overview"] as const,
  movies: ["catalog", "movies"] as const,
};

export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: ({ signal }) => api<StatusResponse>("/api/status", { signal }),
    refetchInterval: 15_000,
  });
}

/** The Activity page: the latest 100 jobs of each state, the real counts and the health of each model. */
export function useQueue() {
  return useQuery({
    queryKey: keys.queue,
    queryFn: ({ signal }) => api<QueueAnswer>("/api/queue", { signal }),
    refetchInterval: 5_000,
  });
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

export function useCatalogStatus() {
  return useQuery({
    queryKey: keys.catalogStatus,
    queryFn: ({ signal }) => api<CatalogStatus>("/api/catalog/status", { signal }),
    refetchInterval: 30_000,
  });
}

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: ({ signal }) => api<AuthMe>("/api/auth/me", { signal }),
    staleTime: Infinity,
  });
}

/** **Sync now**: read Bazarr again, then everything that shows the library is out of date. */
export function useSyncCatalog() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<unknown>("/api/catalog/sync", { method: "POST", body: {} }),
    onSettled: () => client.invalidateQueries({ queryKey: ["catalog"] }),
  });
}

export function useSignOut() {
  return useMutation({
    mutationFn: () => api<unknown>("/api/auth/logout", { method: "POST", body: {} }),
    onSuccess: () => window.location.assign("/login"),
  });
}

export function useCatalog() {
  return useQuery({
    queryKey: keys.catalog,
    queryFn: ({ signal }) => api<CatalogOverview>("/api/catalog", { signal }),
    refetchInterval: 15_000,
  });
}

export function useMovies() {
  return useQuery({
    queryKey: keys.movies,
    queryFn: ({ signal }) => api<MoviesPage>("/api/catalog/movies", { signal }),
    refetchInterval: 15_000,
  });
}

/** A button that spends model calls says what it is about: translate these, or retry that failed job. */
export type CostRequest =
  | { selector: JobsSelector }
  | { retry: { job_id: string } | { all: true } }
  | { prerequisites: { job_id: string } }
  | { retranslate: { unit_id: number; body: RetranslateBody } }
  | { variant: { unit_id: number; body: VariantBody } };

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

interface RetranslateAnswer {
  ok: boolean;
  estimate_only?: boolean;
  estimate: Estimate;
}

/** What a request would cost (UX §6.4): nothing is made, saved or called. */
export function useEstimate(request: CostRequest | null) {
  return useQuery({
    queryKey: ["estimate", request],
    queryFn: async ({ signal }): Promise<JobsAnswer> => {
      if (request && "retry" in request) {
        return api<JobsAnswer>("/api/queue/retry", { method: "POST", body: { ...request.retry, estimate_only: true }, signal });
      }
      if (request && "prerequisites" in request) {
        return api<JobsAnswer>("/api/queue/request-prerequisites", {
          method: "POST",
          body: { ...request.prerequisites, estimate_only: true },
          signal,
        });
      }
      if (request && ("retranslate" in request || "variant" in request)) {
        const [path, call] = "retranslate" in request ? ["retranslate", request.retranslate] : ["variant", request.variant];
        const answer = await api<RetranslateAnswer>(`/api/units/${call.unit_id}/${path}`, {
          method: "POST",
          body: { ...call.body, estimate_only: true },
          signal,
        });
        return { estimate_only: true, estimate: answer.estimate, planned_total: answer.estimate.jobs, skipped_total: 0, skipped_by_reason: {} };
      }
      return api<JobsAnswer>("/api/jobs", { method: "POST", body: { ...request?.selector, estimate_only: true }, signal });
    },
    enabled: request !== null,
    // A price is only true now: never reuse the one from the last time the box was open.
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** Whatever changes a job changes every number and label that counts or shows jobs. */
function useRefreshJobs() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
      client.invalidateQueries({ queryKey: keys.status }),
      client.invalidateQueries({ queryKey: keys.queue }),
    ]);
}

/** Confirm: make the jobs (or put the failed one back in line); `count` is how many are now in line. */
export function useRequestJobs() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: async (request: CostRequest): Promise<{ count: number }> => {
      if ("retry" in request) {
        const answer = await api<{ ok: boolean; count?: number }>("/api/queue/retry", { method: "POST", body: request.retry });
        if (!answer.ok) throw new Error("Nothing could be retried: the jobs are not failed any more, or their files already have a job.");
        return { count: answer.count ?? 1 };
      }
      if ("prerequisites" in request) {
        const answer = await api<{ requested: string[] }>("/api/queue/request-prerequisites", { method: "POST", body: request.prerequisites });
        return { count: answer.requested.length };
      }
      if ("retranslate" in request || "variant" in request) {
        const [path, call] = "retranslate" in request ? ["retranslate", request.retranslate] : ["variant", request.variant];
        await api<{ ok: boolean }>(`/api/units/${call.unit_id}/${path}`, { method: "POST", body: call.body });
        return { count: 1 };
      }
      const answer = await api<JobsAnswer>("/api/jobs", { method: "POST", body: request.selector });
      return { count: answer.count ?? 0 };
    },
    onSettled: () => void refresh(),
  });
}

/** Cancel a requested or waiting job, or ask a running one to stop at the next batch (no model call, no estimate). */
export function useStopJob() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: (jobId: string) => api<{ ok: boolean }>("/api/queue/stop", { method: "POST", body: { job_id: jobId } }),
    onSettled: () => void refresh(),
  });
}

export function useSeriesPage(id: number) {
  return useQuery({
    queryKey: keys.series(id),
    queryFn: ({ signal }) => api<SeriesPage>(`/api/catalog/series/${id}`, { signal }),
    refetchInterval: 10_000,
  });
}

/** Version, model and age of each episode's file; only series the worker keeps (a stored id) have them. */
export function useSeriesUnits(storedId: number | null | undefined) {
  return useQuery({
    queryKey: keys.seriesUnits(storedId ?? 0),
    queryFn: ({ signal }) => api<LibraryUnits>(`/api/library/series/${storedId}/units`, { signal }),
    enabled: typeof storedId === "number",
    refetchInterval: 30_000,
  });
}

// -- one episode or movie -----------------------------------------------------------------------------------------

export function useUnit(id: number) {
  return useQuery({
    queryKey: keys.unit(id),
    queryFn: ({ signal }) => api<UnitDetail>(`/api/units/${id}`, { signal }),
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
  const params = new URLSearchParams({ lang: query.lang, offset: String(query.offset), limit: String(query.limit) });
  if (query.q.trim()) params.set("q", query.q.trim());
  if (query.at.trim()) params.set("at", query.at.trim());
  return useQuery({
    queryKey: [...keys.unit(id), "cues", query] as const,
    queryFn: ({ signal }) => api<CuesPage>(`/api/units/${id}/cues?${params}`, { signal }),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useUnitContext(id: number, lang: string) {
  return useQuery({
    queryKey: [...keys.unit(id), "context", lang] as const,
    queryFn: ({ signal }) => api<UnitContextAnswer>(`/api/units/${id}/context?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled: lang !== "",
  });
}

export function useVersions(id: number, lang: string) {
  return useQuery({
    queryKey: [...keys.unit(id), "versions", lang] as const,
    queryFn: ({ signal }) => api<VersionsAnswer>(`/api/units/${id}/versions?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled: lang !== "",
  });
}

/** What changed from `against` (default: the version before) to `version`. Read when asked for, never before. */
export function useDiff(id: number, lang: string, version: number | null) {
  return useQuery({
    queryKey: [...keys.unit(id), "diff", lang, version] as const,
    queryFn: ({ signal }) =>
      api<DiffAnswer>(`/api/units/${id}/versions/${version}/diff?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled: version !== null,
  });
}

export function useCompare(id: number, lang: string, version: number) {
  return useQuery({
    queryKey: [...keys.unit(id), "compare", lang, version] as const,
    queryFn: ({ signal }) =>
      api<CompareAnswer>(`/api/units/${id}/versions/${version}/compare?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled: lang !== "",
  });
}

/** What keeping a variant's style as the series' own would flag stale: a dry run of "Use this version" (UX §4). */
export function useAdoptImpact(id: number, lang: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: ["adopt-impact", id, lang, version] as const,
    queryFn: ({ signal }) =>
      api<StyleImpact>(`/api/units/${id}/versions/${version}/adopt`, {
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
      api<{ ok: boolean; recorded: number; applied: Record<string, unknown> }>(`/api/units/${id}/versions/${version}/choose`, {
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
      api<{ ok: boolean; version: number }>(`/api/units/${id}/versions/${args.version}/revert`, {
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
      api<{ ok: boolean; version: number }>(`/api/units/${id}/versions/${args.version}/adopt`, {
        method: "POST",
        body: { lang: args.lang },
      }),
    onSettled: () => void refresh(),
  });
}

// -- style ----------------------------------------------------------------------------------------------------------

/** Whose style it is: a series, or a movie (reached by the movie's unit id). */
export type StyleOwner = { kind: "series" | "movie"; id: number };

const ownerPath = (owner: StyleOwner) => (owner.kind === "series" ? `/api/series/${owner.id}` : `/api/movies/${owner.id}`);

export function useOwnerStyle(owner: StyleOwner | null, lang: string) {
  return useQuery({
    queryKey: ["style", owner, lang] as const,
    queryFn: ({ signal }) => api<StyleOverview>(`${ownerPath(owner!)}/style?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled: owner !== null && lang !== "",
  });
}

/** Presets that fit the series' genres on TMDB (it asks TMDB, so once in a while is enough). */
export function useStyleSuggestions(seriesId: number | null) {
  return useQuery({
    queryKey: ["style", "suggestions", seriesId] as const,
    queryFn: ({ signal }) =>
      api<{ genres: string[]; presets: Array<{ id: string; name: string }> }>(`/api/series/${seriesId}/style/suggestions`, { signal }),
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
        ? api<StyleImpact>(`/api/units/${owner.id}/style`, { method: "PUT", body: { lang, style: draft.style, dry_run: true }, signal })
        : api<StyleImpact>(`${ownerPath(owner)}/style?lang=${encodeURIComponent(lang)}`, {
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

function useRefreshStyle() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["style"] }),
      client.invalidateQueries({ queryKey: ["glossary"] }),
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
      client.invalidateQueries({ queryKey: ["unit"] }),
    ]);
}

export function useSaveStyle(owner: StyleOwner | { kind: "episode"; id: number }, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (draft: StyleDraft) =>
      owner.kind === "episode"
        ? api<StyleImpact>(`/api/units/${owner.id}/style`, { method: "PUT", body: { lang, style: draft.style } })
        : api<StyleImpact>(`${ownerPath(owner)}/style?lang=${encodeURIComponent(lang)}`, {
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
    queryFn: ({ signal }) => api<{ style: Style }>(`/api/units/${unitId}/style`, { signal }),
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
    mutationFn: (request: PreviewRequest) => api<StylePreview>(`${ownerPath(owner)}/style/preview`, { method: "POST", body: request }),
  });
}

// -- settings --------------------------------------------------------------------------------------------------------

/** `GET /api/settings`: every setting by its API name; a key is `**********` with `<field>_configured`, `_last4` and `_status`. */
export type SettingsAnswer = Record<string, unknown>;

export function useSettings() {
  return useQuery({
    queryKey: ["settings"] as const,
    queryFn: ({ signal }) => api<SettingsAnswer>("/api/settings", { signal }),
  });
}

/** Save some settings (a group's own, and a key typed in a box). The answer is the new `GET /api/settings`; it is live at once. */
export function useSaveSettings(onSaved?: () => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<SettingsAnswer>("/api/settings", { method: "POST", body }),
    onSuccess: (answer) => {
      client.setQueryData(["settings"], answer);
      onSaved?.(); // the form starts again from the new values: whoever holds "Saved" must not be the form itself
      // A different provider, address or key changes the list of models, the health of the keys and what the banners say.
      void client.invalidateQueries({ queryKey: ["models"] });
      void client.invalidateQueries({ queryKey: keys.status });
      void client.invalidateQueries({ queryKey: keys.queue });
    },
  });
}

export interface ModelChoice {
  id: string;
  name: string;
}

/** The models the provider in use offers (a bundled list when Gemini cannot be asked). */
export function useModels(provider: string, baseUrl: string) {
  return useQuery({
    queryKey: ["models", provider, baseUrl] as const,
    queryFn: ({ signal }) => api<{ items: ModelChoice[] }>("/api/gemini-models", { signal }),
    staleTime: 60_000,
  });
}

export interface LanguageChoice {
  code: string;
  name: string;
  language: string;
}

/** The languages Bazarr supports; empty and an error when Bazarr cannot be reached. */
export function useLanguages() {
  return useQuery({
    queryKey: ["languages"] as const,
    queryFn: ({ signal }) => api<{ items: LanguageChoice[] }>("/api/languages", { signal }),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export interface ConnectionResult {
  ok: boolean;
  kind: string;
  message: string;
  auth_failed?: boolean;
}

/** Try a key (a typed one, or the stored one when the box is blank) without saving it; the stored key's status follows the answer. */
export function useTestConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<ConnectionResult>("/api/test-connection", { method: "POST", body }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["settings"] });
      void client.invalidateQueries({ queryKey: keys.status });
    },
  });
}

export interface CleanupGroup {
  name: string;
  enabled: boolean;
  window: number | null;
  files: number;
  rows: number;
  bytes: number;
  error: string;
}

export interface CleanupAnswer {
  dry_run: boolean;
  at: number;
  groups: CleanupGroup[];
  total: { files: number; rows: number; bytes: number };
  media_temp: string[];
}

/** **Clean up now**: `dry_run: true` only counts; the worker deletes only when the body says `false` in so many words. */
export function useCleanup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (dryRun: boolean) => api<CleanupAnswer>("/api/cleanup", { method: "POST", body: { dry_run: dryRun } }),
    onSuccess: (answer) => {
      if (!answer.dry_run) void client.invalidateQueries();
    },
  });
}

/** Change the console password: every session ends, and this one is given a fresh cookie. */
export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current: string; new: string }) => api<{ ok: boolean }>("/api/auth/password", { method: "POST", body }),
  });
}

// -- backups and logs -----------------------------------------------------------------------------------------------

export interface BackupFile {
  name: string;
  size: number;
  /** Unix seconds. */
  created_at: number;
}

export function useBackups() {
  return useQuery({
    queryKey: ["backups"] as const,
    queryFn: ({ signal }) => api<{ items: BackupFile[] }>("/api/backups", { signal }),
  });
}

/** A backup of the settings; the keys go in only when asked for. */
export function useCreateBackup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (includeKeys: boolean) => api<{ backup: BackupFile }>("/api/backups", { method: "POST", body: { include_keys: includeKeys } }),
    onSettled: () => void client.invalidateQueries({ queryKey: ["backups"] }),
  });
}

export interface ImportAnswer {
  ok: boolean;
  imported: string[];
  pre_import_backup: BackupFile;
}

/** Restore a backup zip: the worker keeps one of the current settings first, and the running worker adopts the new ones at once. */
export function useImportBackup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api<ImportAnswer>("/api/backups/import", { method: "POST", body: file }),
    onSettled: () => void client.invalidateQueries(),
  });
}

export interface LogEntry {
  /** `2026-11-17T21:58:01Z` (UTC); empty for a line before the first header. */
  timestamp: string;
  level: string;
  message: string;
  /** A trace that belongs to the line, if any. */
  details: string;
}

export function useLogs() {
  return useQuery({
    queryKey: ["logs"] as const,
    queryFn: ({ signal }) => api<{ entries: LogEntry[]; truncated: boolean }>("/api/logs", { signal }),
    refetchInterval: 10_000,
  });
}

export function useClearLogs() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ ok: boolean }>("/api/logs/clear", { method: "POST", body: {} }),
    onSettled: () => void client.invalidateQueries({ queryKey: ["logs"] }),
  });
}

// -- story memory of a series -----------------------------------------------------------------------------------------

export function useSeriesMemory(storedId: number | null | undefined) {
  return useQuery({
    queryKey: ["memory", storedId ?? 0] as const,
    queryFn: ({ signal }) => api<SeriesMemory>(`/api/series/${storedId}/memory`, { signal }),
    enabled: typeof storedId === "number",
    refetchInterval: 30_000,
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
      api<SeriesMemory>(`/api/series/${storedId}/memory`, { method: "PUT", body }),
    onSettled: () => void refresh(),
  });
}

/**
 * Read the facts of these episodes (one model call each) in the background: the answer is the task, which Activity and
 * the box that asked follow. `anchor` is any episode of the series (the request is made through it).
 */
export function useExtractFacts() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (request: { anchor: number; unitIds: number[] }) =>
      api<TaskStarted>(`/api/units/${request.anchor}/context/extract`, {
        method: "POST",
        body: { unit_ids: request.unitIds, background: true },
      }),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.tasks }),
  });
}

/** What the stored facts of an episode say, read when its row is opened. */
export function useUnitFacts(unitId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["unit-facts", unitId] as const,
    queryFn: ({ signal }) => api<UnitFacts>(`/api/units/${unitId}/facts`, { signal }),
    enabled,
  });
}

const ACTIVE_TASK = (task: BackgroundTask) => task.state === "queued" || task.state === "running";

/**
 * The admin's long requests, newest state first. It looks again every 2 s while one is active (to show progress) and
 * every 15 s otherwise. When a task that was active is finished, whatever it stored (facts, proposals) is read again.
 */
export function useTasks() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.tasks,
    queryFn: ({ signal }) => api<{ items: BackgroundTask[] }>("/api/tasks", { signal }),
    refetchInterval: (current) => (current.state.data?.items.some(ACTIVE_TASK) ? 2_000 : 15_000),
    retry: false,
  });
  const active = useRef<Set<string>>(new Set());
  useEffect(() => {
    const items = query.data?.items;
    if (!items) return;
    const finished = items.some((task) => active.current.has(task.id) && !ACTIVE_TASK(task));
    active.current = new Set(items.filter(ACTIVE_TASK).map((task) => task.id));
    if (finished) {
      for (const queryKey of [["memory"], ["unit-facts"], ["suggestions"], ["catalog"], ["library"], ["unit"]]) {
        void client.invalidateQueries({ queryKey });
      }
    }
  }, [query.data, client]);
  return query;
}

/** Stop a task: a queued one never starts, a running one stops before its next item (what it stored stays). */
export function useCancelTask() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => api<BackgroundTask>(`/api/tasks/${taskId}/cancel`, { method: "POST", body: {} }),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.tasks }),
  });
}

// -- glossary and characters ---------------------------------------------------------------------------------------

export function useGlossary(owner: StyleOwner | null, lang: string) {
  return useQuery({
    queryKey: ["glossary", owner, lang] as const,
    queryFn: ({ signal }) => api<GlossaryAnswer>(`${ownerPath(owner!)}/glossary?lang=${encodeURIComponent(lang)}`, { signal }),
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
      api<GlossarySave>(`${ownerPath(owner)}/glossary?lang=${encodeURIComponent(lang)}`, {
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
      api<GlossarySave>(`${ownerPath(owner)}/glossary?lang=${encodeURIComponent(lang)}`, { method: "PUT", body }),
    onSettled: () => void refresh(),
  });
}

export function useGlossaryHistory(owner: StyleOwner, lang: string, enabled: boolean) {
  return useQuery({
    queryKey: ["glossary", owner, lang, "history"] as const,
    queryFn: ({ signal }) =>
      api<{ lang: string; items: GlossaryRevision[] }>(`${ownerPath(owner)}/glossary/history?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled,
    gcTime: 0,
  });
}

/** What restoring a version would flag stale: a dry run that counts, restores nothing and costs no model call. */
export function useRestoreImpact(owner: StyleOwner, lang: string, version: number | null) {
  return useQuery({
    queryKey: ["glossary-restore-impact", owner, lang, version] as const,
    queryFn: ({ signal }) =>
      api<GlossaryChange>(`${ownerPath(owner)}/glossary/restore`, { method: "POST", body: { lang, version, dry_run: true }, signal }),
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
      api<GlossaryChange & { restored: number }>(`${ownerPath(owner)}/glossary/restore`, { method: "POST", body: { lang, version } }),
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
      api<GlossaryChange & { from: string }>(`/api/series/${seriesId}/glossary/copy`, {
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
      api<GlossaryChange & { from: string }>(`/api/series/${seriesId}/glossary/copy`, { method: "POST", body: { lang, ...args } }),
    onSettled: () => void refresh(),
  });
}

/** What saving a card would flag stale; `replaces` is the name it had when it is renamed. */
export function useCardImpact(owner: StyleOwner, lang: string, request: { card: CharacterCard; replaces?: string } | null, enabled: boolean) {
  return useQuery({
    queryKey: ["card-impact", owner, lang, request] as const,
    queryFn: ({ signal }) =>
      api<StyleImpact>(`${ownerPath(owner)}/cards?lang=${encodeURIComponent(lang)}`, {
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

/** Save a character: its Name goes into the glossary and its Voice into its card, in one request the worker writes in one transaction (UX §6.3). */
export function useSaveCharacter(owner: StyleOwner, lang: string) {
  const refresh = useRefreshStyle();
  return useMutation({
    mutationFn: (parts: { glossary: GlossaryDoc | null; card: { card: CharacterCard; replaces?: string } | null }) =>
      api<{ ok: boolean }>(`${ownerPath(owner)}/characters?lang=${encodeURIComponent(lang)}`, {
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
      api<{ ok: boolean }>(`${ownerPath(owner)}/characters?lang=${encodeURIComponent(lang)}`, {
        method: "PUT",
        body: { ...(parts.glossary ? { glossary: parts.glossary } : {}), ...(parts.card !== null ? { delete_card: parts.card } : {}) },
      }),
    onSettled: () => void refresh(),
  });
}

/** How many cues the target has: one cheap read of the cue list, for a line that says what a range would overwrite. */
export function useCueTotal(id: number, lang: string) {
  return useCues(id, { lang, q: "", offset: 0, at: "", limit: 1 }, lang !== "");
}

export function useCorrections(id: number, lang: string) {
  return useQuery({
    queryKey: [...keys.unit(id), "corrections", lang] as const,
    queryFn: ({ signal }) => api<{ items: Correction[] }>(`/api/units/${id}/corrections?lang=${encodeURIComponent(lang)}`, { signal }),
    enabled: lang !== "",
  });
}

/** What a change to one unit makes out of date: its own pages, and the labels and versions of the lists. */
function useRefreshUnit(id: number) {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.unit(id) }),
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
    ]);
}

/** Save a cue by hand: a new version is written at once (no model call) and the cue is locked. */
export function useEditCue(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (edit: { lang: string; cue: number; text: string }) =>
      api<{ version: number; warnings?: string[] }>(`/api/units/${id}/corrections`, {
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

export interface CorrectionAnswer {
  ok: boolean;
  scope?: CorrectionScope;
  ids?: number[];
  version?: number;
  warnings?: string[];
  /** For a correction that writes the glossary: how many translations it flags stale. Nothing is retranslated. */
  impact?: { stale_now: number; stale_after: number; would_stale: number };
}

/** Add a correction. It costs nothing now: it acts on the next retranslation (a cue edit writes a version at once). */
export function useAddCorrection(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (correction: NewCorrection) =>
      api<CorrectionAnswer>(`/api/units/${id}/corrections`, { method: "POST", body: correction }),
    onSettled: () => void refresh(),
  });
}

/** Take a correction off; for a cue edit that unlocks the cue and leaves the file as it is. */
export function useRemoveCorrection(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (correctionId: number) =>
      api<unknown>(`/api/units/${id}/corrections/${correctionId}/remove`, { method: "POST", body: {} }),
    onSettled: () => void refresh(),
  });
}

/** Delete the target file; the version stays in History and can be restored. */
export function useRemoveOutput(id: number) {
  const refresh = useRefreshUnit(id);
  return useMutation({
    mutationFn: (lang: string) => api<{ ok: boolean }>(`/api/units/${id}/output/remove`, { method: "POST", body: { lang } }),
    onSettled: () => void refresh(),
  });
}

/** Auto-request for one series saves the moment it is touched (UX §2). */
export function useSetAutoRequest(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (auto_request: "" | "on" | "off") =>
      api<unknown>(`/api/catalog/series/${id}/policy`, { method: "PUT", body: { auto_request } }),
    onSettled: () => client.invalidateQueries({ queryKey: ["catalog"] }),
  });
}

/** **Continue**: let `auto_request` go past the first episodes of a series whose glossary nobody has reviewed (FR-15.12). */
export function useReleaseBootstrap(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (released: boolean) =>
      api<{ ok: boolean; released: boolean }>(`/api/catalog/series/${id}/bootstrap`, { method: "PUT", body: { released } }),
    onSettled: () => client.invalidateQueries({ queryKey: ["catalog"] }),
  });
}

/** Ignore or stop ignoring: a decision, not a request, so it costs nothing and needs no estimate. */
export function useIgnore() {
  const refresh = useRefreshJobs();
  return useMutation({
    mutationFn: ({ ignore, ...selector }: { ignore: boolean } & JobsSelector) =>
      api<{ ok: boolean; changed: number }>(ignore ? "/api/catalog/ignore" : "/api/catalog/unignore", {
        method: "POST",
        body: selector,
      }),
    onSettled: () => void refresh(),
  });
}

// -- Review inbox ---------------------------------------------------------------------------------------------------------

/** How many suggestions wait, for the badge on the nav. Cheap on purpose: it is asked on every page. */
export function useSuggestionSummary() {
  return useQuery({
    queryKey: ["suggestions", "summary"] as const,
    queryFn: ({ signal }) => api<SuggestionSummary>("/api/suggestions/summary", { signal }),
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
      if (status !== "decided") return api<SuggestionList>(`/api/suggestions?status=${status}`, { signal });
      const [accepted, rejected] = await Promise.all([
        api<SuggestionList>("/api/suggestions?status=accepted", { signal }),
        api<SuggestionList>("/api/suggestions?status=rejected", { signal }),
      ]);
      const items: Suggestion[] = [...accepted.items, ...rejected.items].sort((a, b) => b.decided_at - a.decided_at);
      return { ...accepted, items };
    },
    refetchInterval: 30_000,
  });
}

/** What accepting this suggestion (as edited, when `payload` is given) would flag stale: a dry run, nothing is written (FR-11.1.3). */
export function useSuggestionImpact(id: number, payload: SuggestionPayload | null, enabled: boolean) {
  return useQuery({
    queryKey: ["suggestion-impact", id, payload] as const,
    queryFn: ({ signal }) =>
      api<SuggestionDecision>(`/api/suggestions/${id}/accept`, {
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
      api<BulkAnswer>("/api/suggestions/bulk", { method: "POST", body: { series_id: seriesId, dry_run: true }, signal }),
    enabled,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

/** A decision changes the badge, the lists, and (an accept or an undo) the glossary and every label that says Stale. */
function useRefreshSuggestions() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["suggestions"] }),
      client.invalidateQueries({ queryKey: ["suggestion-impact"] }),
      client.invalidateQueries({ queryKey: ["suggestion-bulk-impact"] }),
      client.invalidateQueries({ queryKey: ["glossary"] }),
      client.invalidateQueries({ queryKey: ["style"] }),
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
      client.invalidateQueries({ queryKey: ["unit"] }),
    ]);
}

/** Accept, or edit then accept (`payload`). The glossary changes; translations made without it are flagged stale, never redone. */
export function useAcceptSuggestion() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (args: { id: number; payload?: SuggestionPayload }) =>
      api<SuggestionDecision>(`/api/suggestions/${args.id}/accept`, { method: "POST", body: args.payload ? { payload: args.payload } : {} }),
    onSettled: () => void refresh(),
  });
}

/** Reject: the same content is never proposed again. */
export function useRejectSuggestion() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (id: number) => api<SuggestionDecision>(`/api/suggestions/${id}/reject`, { method: "POST", body: {} }),
    onSettled: () => void refresh(),
  });
}

/** Put back what an accept replaced; the suggestion returns to the inbox. */
export function useUndoSuggestion() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (id: number) => api<SuggestionDecision>(`/api/suggestions/${id}/undo`, { method: "POST", body: {} }),
    onSettled: () => void refresh(),
  });
}

/** Accept every pending suggestion of one series; `expect` is the number the admin confirmed. */
export function useBulkAccept() {
  const refresh = useRefreshSuggestions();
  return useMutation({
    mutationFn: (args: { seriesId: number; expect: number }) =>
      api<BulkAnswer>("/api/suggestions/bulk", { method: "POST", body: { series_id: args.seriesId, expect: args.expect } }),
    onSettled: () => void refresh(),
  });
}
