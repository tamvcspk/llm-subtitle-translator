import type { CatalogStatus, StatusResponse } from "../api/types";
import { ago, clock } from "./format";

export interface StripItem {
  id: string;
  text: string;
  /** Something a person should look at, but not an alarm (alarms are banners). */
  notable?: boolean;
}

/** The small, always-there line that replaces the Dashboard (UX §4.2). */
export function deriveStrip(
  status: StatusResponse | undefined,
  catalog: CatalogStatus | undefined,
  nowMs: number = Date.now(),
): StripItem[] {
  const items: StripItem[] = [];
  if (status) {
    items.push({ id: "running", text: `Running ${status.queue.processing}` });
    // The same words as the tabs of Activity: Requested is what is in line, Waiting is what waits for something.
    items.push({ id: "requested", text: `Requested ${status.queue.pending}` });
    items.push({ id: "waiting", text: `Waiting ${status.queue.deferred}` });
  }
  if (catalog) items.push({ id: "synced", text: `Synced ${ago(catalog.synced_at, nowMs)}` });
  for (const provider of status?.providers ?? []) {
    if (provider.state === "exhausted" || provider.state === "open") {
      const until = provider.until ? ` until ${clock(provider.until)}` : "";
      const what = provider.state === "exhausted" ? "quota spent" : "paused after errors";
      items.push({ id: `provider:${provider.model}`, text: `${provider.model} ${what}${until}`, notable: true });
    }
  }
  return items;
}
