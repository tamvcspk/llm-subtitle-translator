import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import type { AnswerOf } from "../types";
import { keys } from "./keys";

export function useSettings() {
  return useQuery({
    queryKey: ["settings"] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/settings", "get">>("/api/settings", { signal }),
  });
}

/** Save some settings (a group's own, and a key typed in a box). The answer is the new `GET /api/settings`; it is live at once. */
export function useSaveSettings(onSaved?: () => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<AnswerOf<"/api/settings", "post">>("/api/settings", { method: "POST", body }),
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

/** The models the provider in use offers (a bundled list when Gemini cannot be asked). */
export function useModels(provider: string, baseUrl: string) {
  return useQuery({
    queryKey: ["models", provider, baseUrl] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/gemini-models", "get">>("/api/gemini-models", { signal }),
    staleTime: 60_000,
  });
}

/** The languages Bazarr supports; empty and an error when Bazarr cannot be reached. */
export function useLanguages() {
  return useQuery({
    queryKey: ["languages"] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/languages", "get">>("/api/languages", { signal }),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** Try a key (a typed one, or the stored one when the box is blank) without saving it; the stored key's status follows the answer. */
export function useTestConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<AnswerOf<"/api/test-connection", "post">>("/api/test-connection", { method: "POST", body }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["settings"] });
      void client.invalidateQueries({ queryKey: keys.status });
    },
  });
}

/** **Clean up now**: `dry_run: true` only counts; the worker deletes only when the body says `false` in so many words. */
export function useCleanup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (dryRun: boolean) => api<AnswerOf<"/api/cleanup", "post">>("/api/cleanup", { method: "POST", body: { dry_run: dryRun } }),
    onSuccess: (answer) => {
      if (!answer.dry_run) void client.invalidateQueries();
    },
  });
}

/** Change the console password: every session ends, and this one is given a fresh cookie. */
export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current: string; new: string }) => api<AnswerOf<"/api/auth/password", "post">>("/api/auth/password", { method: "POST", body }),
  });
}

// -- backups and logs -----------------------------------------------------------------------------------------------

export function useBackups() {
  return useQuery({
    queryKey: ["backups"] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/backups", "get">>("/api/backups", { signal }),
  });
}

/** A backup of the settings; the keys go in only when asked for. */
export function useCreateBackup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (includeKeys: boolean) => api<AnswerOf<"/api/backups", "post">>("/api/backups", { method: "POST", body: { include_keys: includeKeys } }),
    onSettled: () => void client.invalidateQueries({ queryKey: ["backups"] }),
  });
}

/** Restore a backup zip: the worker keeps one of the current settings first, and the running worker adopts the new ones at once. */
export function useImportBackup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api<AnswerOf<"/api/backups/import", "post">>("/api/backups/import", { method: "POST", body: file }),
    onSettled: () => void client.invalidateQueries(),
  });
}

export function useLogs() {
  return useQuery({
    queryKey: ["logs"] as const,
    queryFn: ({ signal }) => api<AnswerOf<"/api/logs", "get">>("/api/logs", { signal }),
    refetchInterval: 10_000,
  });
}

export function useClearLogs() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<AnswerOf<"/api/logs/clear", "post">>("/api/logs/clear", { method: "POST", body: {} }),
    onSettled: () => void client.invalidateQueries({ queryKey: ["logs"] }),
  });
}
