import type { CatalogStatus, StatusResponse } from "../api/types";
import { ago } from "./format";

export type BannerAction = { label: string; kind: "navigate"; to: string } | { label: string; kind: "sync" };

export interface Banner {
  id: string;
  tone: "red" | "yellow";
  message: string;
  action: BannerAction;
}

const OPEN_CONNECTIONS: BannerAction = { label: "Open Connections", kind: "navigate", to: "/settings" };

/**
 * What stops every job must be visible on every page (UX §3 rule 10, §4.2).
 * A spent daily quota is not here on purpose: it is a normal state, shown by the status strip.
 */
export function deriveBanners(
  status: StatusResponse | undefined,
  catalog: CatalogStatus | undefined,
  nowMs: number = Date.now(),
): Banner[] {
  const banners: Banner[] = [];

  const warnings = (status?.settings.key_warnings ?? []).filter(
    (warning) => warning.kind === "key-invalid" || warning.kind === "model-not-found",
  );
  for (const warning of warnings) {
    banners.push({
      id: `${warning.kind}:${warning.key_id ?? warning.model ?? warning.provider ?? ""}`,
      tone: "red",
      message: `${warning.message} Jobs are waiting.`,
      action: OPEN_CONNECTIONS,
    });
  }
  // A provider can be `invalid` before a warning exists (the key table is updated a moment later).
  if (warnings.length === 0 && status?.providers.some((provider) => provider.state === "invalid")) {
    banners.push({
      id: "provider-invalid",
      tone: "red",
      message: "The provider refused its key or model. Jobs are waiting.",
      action: OPEN_CONNECTIONS,
    });
  }

  if (catalog?.status === "kept" && catalog.shrink) {
    // Bazarr answered; the answer is set aside on purpose because it listed far fewer items. Accepting is on Activity.
    const warning = catalog.warning ? ` ${catalog.warning}` : "";
    banners.push({
      id: "catalog-shrunk",
      tone: "yellow",
      message: `Showing the library as of ${ago(catalog.synced_at, nowMs)}.${warning}`,
      action: { label: "Open Activity", kind: "navigate", to: "/activity" },
    });
  } else if (catalog?.status === "kept") {
    const warning = catalog.warning ? ` ${catalog.warning}` : "";
    banners.push({
      id: "catalog-kept",
      tone: "yellow",
      message: `Bazarr could not be read. Showing the library as of ${ago(catalog.synced_at, nowMs)}.${warning}`,
      action: { label: "Sync now", kind: "sync" },
    });
  }
  return banners;
}
