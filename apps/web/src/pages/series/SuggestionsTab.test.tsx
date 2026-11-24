import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { episode, seriesPage } from "../../test/fixtures";
import { serve, show } from "../../test/render";

const base = {
  "/api/status": {
    body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } },
  },
  "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/catalog/series/1": { body: seriesPage([episode(1), episode(2)]) },
};

describe("Series Suggestions tab", () => {
  it("shows the pending count on the tab and renders the filtered review inbox inline", async () => {
    serve({
      ...base,
      "/api/suggestions/summary": {
        body: {
          counts: { pending: 2, accepted: 0, rejected: 0 },
          pending: 2,
          series: [{ series_id: 9, series: "Arcane", pending: 1 }],
        },
      },
      "/api/suggestions?series=9&status=accepted": {
        body: {
          status: "accepted",
          counts: { pending: 1, accepted: 2, rejected: 0 },
          series: [{ series_id: 9, series: "Arcane", pending: 1 }],
          items: [
            { id: 10, series_id: 9, series: "Arcane", lang: "vi", kind: "term", source: "facts", status: "accepted", confidence: 0.9, payload: { source: "A", target: "a" }, evidence: [], sources: [], created_at: 1_700_000_000, decided_at: 1_700_000_010, note: "", undo: null },
            { id: 11, series_id: 9, series: "Arcane", lang: "vi", kind: "term", source: "facts", status: "accepted", confidence: 0.9, payload: { source: "B", target: "b" }, evidence: [], sources: [], created_at: 1_700_000_000, decided_at: 1_700_000_020, note: "", undo: null },
          ],
        },
      },
      "/api/suggestions?series=9&status=rejected": {
        body: {
          status: "rejected",
          counts: { pending: 1, accepted: 2, rejected: 0 },
          series: [{ series_id: 9, series: "Arcane", pending: 1 }],
          items: [],
        },
      },
      "/api/suggestions?series=9&status=pending": {
        body: {
          status: "pending",
          counts: { pending: 1, accepted: 0, rejected: 0 },
          series: [{ series_id: 9, series: "Arcane", pending: 1 }],
          items: [
            {
              id: 42,
              series_id: 9,
              series: "Arcane",
              lang: "vi",
              kind: "address_rule",
              source: "facts",
              status: "pending",
              confidence: 0.9,
              payload: { a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị" },
              evidence: [],
              sources: [],
              created_at: 1_700_000_000,
              decided_at: 0,
              note: "",
              undo: null,
            },
          ],
        },
      },
    });
    show("/series/1/suggestions");
    const tab = await screen.findByRole("tab", { name: "Suggestions (1)" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    const card = await screen.findByRole("listitem", { name: "Form of address: Vi ↔ Powder" });
    expect(within(card).getByText("Vi ↔ Powder")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Accept Vi ↔ Powder" })).toBeInTheDocument();
    // F2: History tab shows the series' decided count even while viewing Pending.
    expect(await screen.findByRole("tab", { name: "History 2" })).toBeInTheDocument();
  });

  it("updates the inner History tab count immediately after Accept all while staying on Pending", async () => {
    const item = {
      id: 42,
      series_id: 9,
      series: "Arcane",
      lang: "vi",
      kind: "address_rule",
      source: "facts",
      status: "pending",
      confidence: 0.9,
      payload: { a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị" },
      evidence: [],
      sources: [],
      created_at: 1_700_000_000,
      decided_at: 0,
      note: "",
      undo: null,
    };
    let accepted = false;
    serve({
      ...base,
      "/api/suggestions/summary": () => ({
        body: {
          counts: { pending: accepted ? 0 : 1, accepted: accepted ? 1 : 0, rejected: 0 },
          pending: accepted ? 0 : 1,
          series: accepted ? [] : [{ series_id: 9, series: "Arcane", pending: 1 }],
        },
      }),
      "/api/suggestions?series=9&status=pending": () => ({
        body: {
          status: "pending",
          counts: { pending: accepted ? 0 : 1, accepted: accepted ? 1 : 0, rejected: 0 },
          series: accepted ? [] : [{ series_id: 9, series: "Arcane", pending: 1 }],
          items: accepted ? [] : [item],
        },
      }),
      "/api/suggestions?series=9&status=accepted": () => ({
        body: {
          status: "accepted",
          counts: { pending: accepted ? 0 : 1, accepted: accepted ? 1 : 0, rejected: 0 },
          series: accepted ? [] : [{ series_id: 9, series: "Arcane", pending: 1 }],
          items: accepted ? [{ ...item, status: "accepted", decided_at: 1_700_000_050 }] : [],
        },
      }),
      "/api/suggestions?series=9&status=rejected": () => ({
        body: {
          status: "rejected",
          counts: { pending: accepted ? 0 : 1, accepted: accepted ? 1 : 0, rejected: 0 },
          series: [],
          items: [],
        },
      }),
      "/api/suggestions/bulk": (body: Record<string, unknown> | null) => {
        if (body?.dry_run) return { body: { ok: true, saved: false, count: 1, would_stale: 0 } };
        accepted = true;
        return { body: { ok: true, saved: true, count: 1, would_stale: 0, failed: [], series: "Arcane" } };
      },
    });
    show("/series/1/suggestions");
    expect(await screen.findByRole("tab", { name: "Pending 1" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "History 0" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Accept all in Arcane" }));
    const sheet = await screen.findByRole("dialog", { name: "Accept all · Arcane" });
    await userEvent.click(await within(sheet).findByRole("button", { name: "Accept 1" }));

    expect(await screen.findByRole("tab", { name: "Pending 0" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "History 1" })).toBeInTheDocument();
  });

  it("explains when the worker keeps nothing for the series yet", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { series_id: null }) } });
    show("/series/1/suggestions");
    expect(await screen.findByText(/keeps nothing for this series yet/)).toBeInTheDocument();
  });
});
