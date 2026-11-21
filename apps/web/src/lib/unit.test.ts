import { describe, expect, it } from "vitest";
import { episode } from "../test/unit-fixtures";
import { unitTitle } from "./unit";

describe("unitTitle", () => {
  it("is the code and the title of an episode", () => {
    expect(unitTitle(episode({ season: 1, episode: 1, title: "Oil and Water" }))).toBe("S01E01 · Oil and Water");
  });

  it("is only the code when Bazarr gives the episode no title, never `S01E01 ·`", () => {
    expect(unitTitle(episode({ season: 1, episode: 1, title: "" }))).toBe("S01E01");
  });

  it("is the title of a movie", () => {
    expect(unitTitle(episode({ series: null, series_id: null, season: null, episode: null, title: "Heat" }))).toBe("Heat");
  });
});
