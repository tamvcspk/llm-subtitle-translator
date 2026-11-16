import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { resetSession } from "../api/session";
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

  it("offers Library, Activity and Settings, and no Review before P13", async () => {
    serve(healthy);
    show();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Library");
    expect(nav).toHaveTextContent("Activity");
    expect(nav).toHaveTextContent("Settings");
    expect(nav).not.toHaveTextContent("Review");
    await screen.findByText("Running 1");
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
});
