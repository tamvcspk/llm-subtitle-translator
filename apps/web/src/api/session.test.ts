import { describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { RETURN_KEY, isSafeReturn, rememberReturn } from "./session";

describe("return path", () => {
  it("accepts only a fragment of this page", () => {
    expect(isSafeReturn("#/series/12")).toBe(true);
    expect(isSafeReturn("#/series/12?tab=style")).toBe(true);
    expect(isSafeReturn("//evil.example/")).toBe(false);
    expect(isSafeReturn("https://evil.example/")).toBe(false);
    expect(isSafeReturn("/series/12")).toBe(false);
    expect(isSafeReturn("#/a b")).toBe(false);
    expect(isSafeReturn("#/<script>")).toBe(false);
    expect(isSafeReturn("#" + "a".repeat(600))).toBe(false);
    expect(isSafeReturn(null)).toBe(false);
  });

  it("stores a good hash for /login and drops a bad one", () => {
    const storage = { setItem: vi.fn() };
    rememberReturn("#/activity", storage);
    expect(storage.setItem).toHaveBeenCalledWith(RETURN_KEY, "#/activity");
    rememberReturn("javascript:alert(1)", storage);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
  });

  it("survives a storage that throws", () => {
    const storage = {
      setItem: () => {
        throw new Error("quota");
      },
    };
    expect(() => rememberReturn("#/activity", storage)).not.toThrow();
  });
});

describe("api", () => {
  function reply(status: number, body: unknown): void {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
    );
  }

  it("returns the JSON of a good answer", async () => {
    reply(200, { ok: true });
    await expect(api<{ ok: boolean }>("/api/x")).resolves.toEqual({ ok: true });
  });

  it("sends a JSON body with the right header", async () => {
    reply(200, {});
    await api("/api/x", { method: "POST", body: { a: 1 } });
    const [, init] = vi.mocked(fetch).mock.calls[0] ?? [];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"a":1}');
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("raises ApiError with the server's message", async () => {
    reply(400, { error: "bad range" });
    await expect(api("/api/x")).rejects.toMatchObject({ status: 400, message: "bad range" });
  });
});
