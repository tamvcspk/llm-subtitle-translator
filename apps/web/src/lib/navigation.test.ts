import { describe, expect, it } from "vitest";
import { clearNavigationMemory, isLibraryPath, lastLibraryPath, readHomeScroll, rememberLibraryPath, saveHomeScroll } from "./navigation";

describe("navigation memory", () => {
  it("recognises library, series and unit routes", () => {
    expect(isLibraryPath("/library")).toBe(true);
    expect(isLibraryPath("/series/4/glossary")).toBe(true);
    expect(isLibraryPath("/unit/12/subtitles")).toBe(true);
    expect(isLibraryPath("/review")).toBe(false);
    expect(isLibraryPath("/activity")).toBe(false);
  });

  it("remembers the last library path with query string and ignores non-library paths", () => {
    expect(lastLibraryPath()).toBe("/library");
    rememberLibraryPath("/series/9/glossary", "?lang=vi");
    expect(lastLibraryPath()).toBe("/series/9/glossary?lang=vi");
    rememberLibraryPath("/review", "");
    expect(lastLibraryPath()).toBe("/series/9/glossary?lang=vi");
    clearNavigationMemory();
    expect(lastLibraryPath()).toBe("/library");
  });

  it("stores and restores positive home scroll offsets", () => {
    expect(readHomeScroll()).toBe(0);
    saveHomeScroll(420.6);
    expect(readHomeScroll()).toBe(421);
    saveHomeScroll(-10);
    expect(readHomeScroll()).toBe(0);
  });
});
