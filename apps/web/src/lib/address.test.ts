import { describe, expect, it } from "vitest";
import { genericProblem, isGenericAddress } from "./address";

describe("generic forms of address", () => {
  it("knows the relatives and the titles, with or without a article, a mark or case", () => {
    for (const word of ["Mom", "mom", " DAD ", "the Sir", "my son", "Ma'am", "ma’am", "Mom!", "Honey"]) expect(isGenericAddress(word), word).toBe(true);
  });

  it("does not take a proper name that only holds one", () => {
    for (const name of ["Lois", "Mother Teresa", "Dr. Doc Brown", "Sirius", "Sonny", "Ida"]) expect(isGenericAddress(name), name).toBe(false);
  });

  it("says why in the words of the form, and nothing for a name", () => {
    expect(genericProblem("Mom")).toBe('"Mom" is a relative form of address, not a character\'s name.');
    expect(genericProblem("Lois")).toBeNull();
  });
});
