import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { episode, seriesPage, target } from "../test/fixtures";
import { sent, serve, show } from "../test/render";

const base = {
  "/api/status": { body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } } },
  "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/library/series/9/units": { body: { languages: ["vi"], units: [] } },
};

const held = target({ reason: "bootstrap", text: "Missing · Waiting for glossary review" });

afterEach(() => vi.unstubAllGlobals());

describe("A series whose auto-request is waiting for its glossary (FR-15.12)", () => {
  it("says why, names what is waiting, and still lets the admin translate the held episode", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": {
        body: seriesPage([episode(1, { targets: [target({ job: "requested", text: "Missing · Requested" })] }), episode(3, { targets: [held] })], {
          bootstrap: { locked: true, limit: 2, pending: 4, released: false, held: 1 },
        }),
      },
    });
    show("/series/1/episodes");
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Auto-request is waiting for this series' glossary");
    expect(notice).toHaveTextContent("only its first 2 episodes were requested");
    expect(notice).toHaveTextContent("1 more waits until the suggestions are reviewed (4 waiting)");
    expect(within(notice).getByRole("link", { name: "Review suggestions" })).toHaveAttribute("href", "/review");
    const row = screen.getByText("S01E03").closest("li") as HTMLElement;
    expect(within(row).getByText("Missing · Waiting for glossary review")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Translate S01E03" })).toBeInTheDocument(); // only the policy waits
  });

  it("Continue lets the rest through, and says when", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": {
        body: seriesPage([episode(3, { targets: [held] })], { bootstrap: { locked: true, limit: 2, pending: 0, released: false, held: 1 } }),
      },
      "/api/catalog/series/1/bootstrap": { body: { ok: true, released: true } },
    });
    show("/series/1/episodes");
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Continue lets the rest through at the next sync.");
    expect(within(notice).queryByRole("link", { name: "Review suggestions" })).toBeNull(); // nothing to review
    await userEvent.click(within(notice).getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(sent("/api/catalog/series/1/bootstrap")).toEqual([{ released: true }]));
  });

  it("shows nothing when the series is not held", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)]) } });
    show("/series/1/episodes");
    await screen.findByRole("heading", { name: "Arcane" });
    expect(screen.queryByText("Auto-request is waiting for this series' glossary")).toBeNull();
  });
});
