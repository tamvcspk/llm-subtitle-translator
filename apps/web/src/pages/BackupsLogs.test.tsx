import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { sent, serve, show } from "../test/render";

const base = {
  "/api/status": {
    body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } },
  },
  "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
};

const backup = (name: string, created_at: number, size: number) => ({ name, path: `/state/backups/${name}`, size, created_at });

afterEach(() => vi.unstubAllGlobals());

/** The file box of Mantine is a button over a hidden input: files go to the input. */
const fileBox = () => document.querySelector('input[type="file"]') as HTMLInputElement;

describe("Backups", () => {
  const listed = { items: [backup("backup-2026-11-16.zip", 1_800_000_000, 14_336), backup("backup-2026-11-10.zip", 1_799_000_000, 16_384)] };

  it("lists the backups with a download link the server serves, and no path of the server", async () => {
    serve({ ...base, "/api/backups": { body: listed } });
    show("/backups");
    const list = await screen.findByRole("list", { name: "Backups" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    const link = within(list).getByRole("link", { name: "Download backup-2026-11-16.zip" });
    expect(link).toHaveAttribute("href", "/api/backups/download?name=backup-2026-11-16.zip");
    expect(link).toHaveAttribute("download", "backup-2026-11-16.zip");
    expect(within(list).getByText(/14\.0 KB/)).toBeInTheDocument();
    expect(screen.queryByText(/\/state\/backups/)).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings");
  });

  it("creates a backup without keys unless asked, and says its name", async () => {
    serve({
      ...base,
      "/api/backups": (body: Record<string, unknown> | null) => (body === null ? { body: listed } : { body: { backup: backup("backup-new.zip", 1_800_000_100, 2048) } }),
    });
    show("/backups");
    await userEvent.click(await screen.findByRole("button", { name: "Create backup" }));
    expect(await screen.findByText("✓ Created backup-new.zip")).toBeInTheDocument();
    expect(sent("/api/backups").filter((body) => body !== null)).toEqual([{ include_keys: false }]);

    await userEvent.click(screen.getByRole("checkbox", { name: /Include keys/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create backup" }));
    await waitFor(() => expect(sent("/api/backups").filter((body) => body !== null)).toHaveLength(2));
    expect(sent("/api/backups").filter((body) => body !== null)[1]).toEqual({ include_keys: true });
  });

  it("says when there is no backup yet", async () => {
    serve({ ...base, "/api/backups": { body: { items: [] } } });
    show("/backups");
    expect(await screen.findByText("No backup yet.")).toBeInTheDocument();
  });

  it("asks before it imports a backup, sends the file as it is, and says where the old settings went", async () => {
    const calls: Array<{ type: string; size: number }> = [];
    serve({
      ...base,
      "/api/backups": { body: listed },
      "/api/backups/import": { body: { ok: true, imported: ["config/config.json", "postprocess/targets.json"], pre_import_backup: backup("pre-import.zip", 1_800_000_200, 1024) } },
    });
    const real = fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === "/api/backups/import") calls.push({ type: String((init?.headers as Record<string, string>)["Content-Type"]), size: (init?.body as Blob).size });
        return real(input, init);
      }),
    );
    show("/backups");
    const file = new File(["PK-zip-bytes"], "mine.zip", { type: "application/zip" });
    await screen.findByRole("button", { name: "Import backup…" });
    await userEvent.upload(fileBox(), file);
    const sheet = await screen.findByRole("dialog", { name: "Import backup?" });
    expect(within(sheet).getByText(/mine\.zip replaces the saved settings/)).toBeInTheDocument();
    expect(calls).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Import" }));
    expect(await within(sheet).findByText(/Imported config\/config\.json, postprocess\/targets\.json/)).toBeInTheDocument();
    expect(within(sheet).getByText(/kept in pre-import\.zip/)).toBeInTheDocument();
    expect(calls).toEqual([{ type: "application/zip", size: 12 }]);
  });

  it("shows why a backup is refused", async () => {
    serve({ ...base, "/api/backups": { body: listed }, "/api/backups/import": { status: 400, body: { error: "backup is not a valid zip file" } } });
    show("/backups");
    await screen.findByRole("button", { name: "Import backup…" });
    await userEvent.upload(fileBox(), new File(["x"], "bad.zip", { type: "application/zip" }));
    const sheet = await screen.findByRole("dialog", { name: "Import backup?" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Import" }));
    expect(await within(sheet).findByText("backup is not a valid zip file")).toBeInTheDocument();
  });
});

describe("Logs", () => {
  const entries = [
    { timestamp: "2026-11-17T21:40:00Z", level: "ERROR", message: "S01E07 failed after 3 attempts: output limit", details: "Traceback (most recent call last)\nValueError: boom\n" },
    { timestamp: "2026-11-17T21:55:12Z", level: "WARNING", message: "Key 1 exhausted the daily quota, using key 2", details: "" },
    { timestamp: "2026-11-17T21:58:01Z", level: "INFO", message: "S01E04 batch 3/9 translated (40 cues, 1.8 s)", details: "" },
  ];

  it("lists the lines, each with its level, and keeps a trace folded under its line", async () => {
    serve({ ...base, "/api/logs": { body: { entries, truncated: false } } });
    show("/logs");
    const list = await screen.findByRole("list", { name: "Log lines" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(list).getByText("S01E04 batch 3/9 translated (40 cues, 1.8 s)")).toBeInTheDocument();
    expect(within(list).getByText("Trace")).toBeInTheDocument();
    expect(within(list).getAllByText("Trace")).toHaveLength(1);
  });

  it("shows this level and worse, and the lines that match a search", async () => {
    serve({ ...base, "/api/logs": { body: { entries, truncated: false } } });
    show("/logs");
    await screen.findByRole("list", { name: "Log lines" });
    await userEvent.click(within(screen.getByRole("radiogroup", { name: "Level" })).getByRole("radio", { name: "WARNING" }));
    expect(within(screen.getByRole("list", { name: "Log lines" })).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByText(/batch 3\/9/)).not.toBeInTheDocument();

    await userEvent.click(within(screen.getByRole("radiogroup", { name: "Level" })).getByRole("radio", { name: "All" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Search" }), "valueerror");
    expect(within(screen.getByRole("list", { name: "Log lines" })).getAllByRole("listitem")).toHaveLength(1);
    await userEvent.clear(screen.getByRole("textbox", { name: "Search" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Search" }), "no such text");
    expect(await screen.findByText("No line matches.")).toBeInTheDocument();
  });

  it("says when older lines are left out and when the log is empty", async () => {
    serve({ ...base, "/api/logs": { body: { entries: [], truncated: true } } });
    show("/logs");
    expect(await screen.findByText("The log is empty.")).toBeInTheDocument();
    expect(screen.getByText(/older ones are in the log file/)).toBeInTheDocument();
  });

  it("clears the log only after a confirmation", async () => {
    serve({ ...base, "/api/logs": { body: { entries, truncated: false } }, "/api/logs/clear": { body: { ok: true } } });
    show("/logs");
    await userEvent.click(await screen.findByRole("button", { name: "Clear logs" }));
    const sheet = await screen.findByRole("dialog", { name: "Clear logs?" });
    expect(sent("/api/logs/clear")).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm clear" }));
    await waitFor(() => expect(sent("/api/logs/clear")).toHaveLength(1));
  });
});
