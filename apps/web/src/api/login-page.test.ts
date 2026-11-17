import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { isSafeReturn } from "./session";

// The sign-in page is a standalone static file (ADR-17): it cannot import this bundle, so its small
// `returnTarget` is cut out of the HTML here and held to the same rule as `isSafeReturn`.
// The build copies `public/` as it is, so this file is `dist/login.html`.
const LOGIN = resolve(process.cwd(), "public/login.html"); // tests run from apps/web

function loadReturnTarget(): (saved: unknown) => string {
  const html = readFileSync(LOGIN, "utf-8");
  const match = html.match(/\/\/ <return-target>([\s\S]*?)\/\/ <\/return-target>/);
  if (!match?.[1]) throw new Error("login.html lost its <return-target> block");
  return new Function(`${match[1]}\nreturn returnTarget;`)() as (saved: unknown) => string;
}

describe("login.html return target", () => {
  const returnTarget = loadReturnTarget();

  it("goes back to the stored fragment", () => {
    expect(returnTarget("#/activity")).toBe("/#/activity");
    expect(returnTarget("#/series/12?tab=style")).toBe("/#/series/12?tab=style");
  });

  it("goes to the console root when nothing, or nothing safe, was stored", () => {
    for (const saved of [null, undefined, "", "/activity", "//evil.example/", "https://evil.example/", "#/a b", "#/<x>", "#" + "a".repeat(600)]) {
      expect(returnTarget(saved)).toBe("/");
    }
  });

  it("agrees with isSafeReturn on every case", () => {
    const cases = ["#/a", "#", "#/a?b=c&d=e%20f", "/a", "javascript:alert(1)", "#/a\nb", "#/é"];
    for (const saved of cases) {
      expect(returnTarget(saved) === `/${saved}`).toBe(isSafeReturn(saved));
    }
  });
});
