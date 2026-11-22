import { describe, expect, it } from "vitest";
import type { CatalogStatus, StatusResponse } from "../api/types";
import { deriveBanners } from "./banners";
import { deriveStrip } from "./strip";

const NOW = 1_800_000_000_000;

function status(overrides: Partial<StatusResponse> = {}): StatusResponse {
  return {
    queue: { pending: 2, processing: 1, deferred: 3, done: 10, failed: 0 },
    daily_quota_pause: null,
    providers: [],
    settings: { key_warnings: [] },
    ...overrides,
  };
}

function catalog(overrides: Partial<CatalogStatus> = {}): CatalogStatus {
  return { synced_at: NOW / 1000 - 120, attempted_at: NOW / 1000, status: "ok", warning: "", items: 40, ...overrides };
}

describe("deriveBanners", () => {
  it("shows nothing when all is well", () => {
    expect(deriveBanners(status(), catalog(), NOW)).toEqual([]);
    expect(deriveBanners(undefined, undefined, NOW)).toEqual([]);
  });

  it("turns a refused key into a red banner that opens Connections", () => {
    const banners = deriveBanners(
      status({
        settings: {
          key_warnings: [{ kind: "key-invalid", key_id: 3, message: "The primary key of gemini was refused." }],
        },
      }),
      catalog(),
      NOW,
    );
    expect(banners).toHaveLength(1);
    expect(banners[0]).toMatchObject({ tone: "red", action: { kind: "navigate", to: "/settings" } });
    expect(banners[0]?.message).toContain("Jobs are waiting.");
  });

  it("does not raise an alarm for a missing base URL or a spent quota", () => {
    const banners = deriveBanners(
      status({
        settings: { key_warnings: [{ kind: "provider-unconfigured", message: "No base URL." }] },
        daily_quota_pause: { retry_at: NOW / 1000 + 3600 },
        providers: [{ model: "m", state: "exhausted", reason: "quota", until: NOW / 1000 + 3600, failures: 0, usable: false }],
      }),
      catalog(),
      NOW,
    );
    expect(banners).toEqual([]);
  });

  it("falls back to one banner when a provider is invalid before any warning exists", () => {
    const banners = deriveBanners(
      status({ providers: [{ model: "m", state: "invalid", reason: "", until: null, failures: 1, usable: false }] }),
      catalog(),
      NOW,
    );
    expect(banners.map((banner) => banner.id)).toEqual(["provider-invalid"]);
  });

  it("warns in yellow, with Sync now, when the catalog was kept", () => {
    const banners = deriveBanners(status(), catalog({ status: "kept", warning: "Bazarr answered with nothing." }), NOW);
    expect(banners).toHaveLength(1);
    expect(banners[0]).toMatchObject({ tone: "yellow", action: { kind: "sync", label: "Sync now" } });
    expect(banners[0]?.message).toBe(
      "Bazarr could not be read. Showing the library as of 2m ago. Bazarr answered with nothing.",
    );
  });

  it("does not say Bazarr could not be read when it answered and the guard set the answer aside", () => {
    const warning = "Bazarr listed 2 items, 4 fewer than the 6 before (more than 20%): the answer is set aside.";
    const banners = deriveBanners(status(), catalog({ status: "kept", warning, shrink: { listed: 2, dropped: 4 } }), NOW);
    expect(banners).toHaveLength(1);
    expect(banners[0]).toMatchObject({ id: "catalog-shrunk", tone: "yellow", action: { kind: "navigate", to: "/activity" } });
    expect(banners[0]?.message).toBe(`Showing the library as of 2m ago. ${warning}`);
    expect(banners[0]?.message).not.toContain("could not be read");
  });
});

describe("deriveStrip", () => {
  it("counts running, requested (in line) and waiting (deferred) as the tabs of Activity do, and the catalog age", () => {
    const items = deriveStrip(status(), catalog(), NOW);
    expect(items.map((item) => item.text)).toEqual(["Running 1", "Requested 2", "Waiting 3", "Synced 2m ago"]);
  });

  it("names a provider that is out, and when it is back", () => {
    const until = NOW / 1000 + 600;
    const items = deriveStrip(
      status({ providers: [{ model: "flash", state: "exhausted", reason: "", until, failures: 0, usable: false }] }),
      undefined,
      NOW,
    );
    const out = items.find((item) => item.id === "provider:flash");
    expect(out?.notable).toBe(true);
    expect(out?.text).toMatch(/^flash quota spent until /);
  });

  it("is empty before anything has loaded", () => {
    expect(deriveStrip(undefined, undefined, NOW)).toEqual([]);
  });
});
