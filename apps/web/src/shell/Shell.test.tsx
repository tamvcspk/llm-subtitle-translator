import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { resetSession } from "../api/session";
import { seriesPage } from "../test/fixtures";
import { serve, show } from "../test/render";

const healthy = {
  "/api/status": {
    body: {
      queue: { pending: 0, processing: 1, deferred: 0, done: 4, failed: 0 },
      daily_quota_pause: null,
      providers: [],
      settings: { key_warnings: [] },
    },
  },
  "/api/catalog/status": {
    body: { synced_at: Date.now() / 1000 - 30, attempted_at: null, status: "ok", warning: "", items: 3 },
  },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/catalog": {
    body: {
      status: { synced_at: null, attempted_at: null, status: "", warning: "", items: 0 },
      languages: ["vi"],
      series: [],
      movies: { count: 0, missing: {}, failed: {}, ignored: {} },
    },
  },
  "/api/catalog/movies": { body: { status: {}, languages: ["vi"], items: [] } },
};

describe("Shell", () => {
  beforeEach(() => resetSession());
  afterEach(() => resetSession());

  it("offers Library, Activity and Settings, and no Review while nothing was ever proposed", async () => {
    serve({ ...healthy, "/api/suggestions/summary": { body: { counts: { pending: 0, accepted: 0, rejected: 0 }, pending: 0, series: [] } } });
    show();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Library");
    expect(nav).toHaveTextContent("Activity");
    expect(nav).toHaveTextContent("Settings");
    await screen.findByText("Running 1");
    expect(nav).not.toHaveTextContent("Review");
  });

  it("shows Review with the number of pending suggestions as its badge, and hides the badge at zero", async () => {
    serve({ ...healthy, "/api/suggestions/summary": { body: { counts: { pending: 3, accepted: 1, rejected: 0 }, pending: 3, series: [] } } });
    const first = show();
    expect(await screen.findByRole("link", { name: "Review 3 pending" })).toHaveAttribute("href", "/review");
    first.unmount();
    serve({ ...healthy, "/api/suggestions/summary": { body: { counts: { pending: 0, accepted: 2, rejected: 1 }, pending: 0, series: [] } } });
    show();
    const link = await screen.findByRole("link", { name: "Review" });
    expect(link).not.toHaveTextContent("0");
  });

  it("moves between pages by link", async () => {
    serve(healthy);
    show();
    await userEvent.click(screen.getByRole("link", { name: "Activity" }));
    expect(await screen.findByRole("heading", { name: "Activity" })).toBeInTheDocument();
  });

  it("sends the root and an unknown route to the Library", async () => {
    serve(healthy);
    show("/nowhere");
    expect(await screen.findByRole("textbox", { name: "Search by title" })).toBeInTheDocument();
  });

  it("shows the kept-catalog banner with Sync now", async () => {
    serve({
      ...healthy,
      "/api/catalog/status": {
        body: { synced_at: Date.now() / 1000 - 3600, attempted_at: null, status: "kept", warning: "Bazarr timed out.", items: 3 },
      },
    });
    show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Bazarr could not be read.");
    expect(within(alert).getByRole("button", { name: "Sync now" })).toBeInTheDocument();
  });

  it("blocks the page with the session overlay when a call answers 401", async () => {
    serve({
      "/api/status": { status: 401, body: { error: "authentication required" } },
      "/api/catalog/status": healthy["/api/catalog/status"],
      "/api/auth/me": healthy["/api/auth/me"],
    });
    show();
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("Your session expired."));
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("remembers the last visited Library route when leaving and returning, and returns to /library on second tap", async () => {
    serve({
      ...healthy,
      "/api/catalog/series/1": { body: seriesPage([]) },
      "/api/library/series/9/units": { body: { languages: ["vi"], units: [] } },
      "/api/queue": { body: { counts: {}, total: 0, daily_quota_pause: null, providers: [], processing: [], pending: [], deferred: [], done: [], failed: [] } },
    });
    show("/series/1/episodes");
    expect(await screen.findByRole("heading", { name: "Arcane" })).toBeInTheDocument();

    // Leave Library area for Activity, then click Library -> returns to /series/1/episodes.
    const nav = screen.getByRole("navigation", { name: "Main" });
    await userEvent.click(within(nav).getByRole("link", { name: "Activity" }));
    expect(await screen.findByRole("heading", { name: "Activity" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Library" })).toHaveAttribute("href", "/series/1/episodes");
    await userEvent.click(within(nav).getByRole("link", { name: "Library" }));
    expect(await screen.findByRole("heading", { name: "Arcane" })).toBeInTheDocument();

    // While already inside Library area, Library link points back to /library.
    expect(within(nav).getByRole("link", { name: "Library" })).toHaveAttribute("href", "/library");
    await userEvent.click(within(nav).getByRole("link", { name: "Library" }));
    expect(await screen.findByRole("textbox", { name: "Search by title" })).toBeInTheDocument();
  });
});
