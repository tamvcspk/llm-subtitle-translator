import { useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";

/** Whatever changes a job changes every number and label that counts or shows jobs. */
export function useRefreshJobs() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
      client.invalidateQueries({ queryKey: keys.status }),
      client.invalidateQueries({ queryKey: keys.queue }),
    ]);
}

export function useRefreshStyle() {
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

/** What a change to one unit makes out of date: its own pages, and the labels and versions of the lists. */
export function useRefreshUnit(id: number) {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.unit(id) }),
      client.invalidateQueries({ queryKey: ["catalog"] }),
      client.invalidateQueries({ queryKey: ["library"] }),
    ]);
}
