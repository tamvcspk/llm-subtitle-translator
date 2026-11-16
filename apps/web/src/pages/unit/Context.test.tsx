import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { serve, show } from "../../test/render";
import { base, cue, cues, episode } from "../../test/unit-fixtures";

const routes = (context: unknown) => ({
  ...base,
  "/api/units/7": { body: episode() },
  "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
  "/api/units/7/context?lang=vi": { body: context },
});

describe("Context tab", () => {
  it("shows what the translation used, what a new one would get, and what is missing", async () => {
    serve(
      routes({
        applicable: true,
        enabled: true,
        used: { version: 2, complete: false, missing: ["S01E03"], changed: true, sections: [{ title: "Story so far", body: "Vi and Powder grew up together." }] },
        now: { approximate: true, sections: [{ title: "Relationship state", body: "Allies." }], missing: ["S01E03", "S01E04"] },
      }),
    );
    show("/unit/7/context");
    const used = await screen.findByRole("region", { name: "Used for the current translation" });
    expect(within(used).getByText("Vi and Powder grew up together.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "If translated now" })).getByText("Allies.")).toBeInTheDocument();
    expect(screen.getByText("Changed since this translation")).toBeInTheDocument();
    expect(screen.getByText("Missing earlier episodes: S01E03, S01E04")).toBeInTheDocument();
    expect(screen.getByText(/only shortened/)).toBeInTheDocument();
  });

  it("says when story memory is off, or the translation was made without it", async () => {
    serve(routes({ applicable: true, enabled: false }));
    show("/unit/7/context");
    expect(await screen.findByText(/Story memory is off for this series/)).toBeInTheDocument();
  });

  it("says a translation made without memory used none", async () => {
    serve(routes({ applicable: true, enabled: true, used: null, now: { approximate: false, sections: [], missing: [] } }));
    show("/unit/7/context");
    expect(await screen.findByText("The current translation was made without story memory.")).toBeInTheDocument();
    expect(screen.getByText("Nothing yet.")).toBeInTheDocument();
  });
});
