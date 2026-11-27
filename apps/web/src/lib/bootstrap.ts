import type { SeriesBootstrap } from "../api/types";

/**
 * Why a series is held, in the four states the yellow box can really be in (FR-15.12, UX 2). The box says what is true
 * and what the admin can do next, not a promise that a review is coming: with no suggestion there is none to wait for.
 */
export type BootstrapMode = "review" | "running" | "memory-off" | "empty";

export function bootstrapMode(bootstrap: SeriesBootstrap): BootstrapMode {
  if (bootstrap.pending > 0) return "review";
  if (bootstrap.open_jobs > 0) return "running";
  if (!bootstrap.memory_on) return "memory-off";
  return "empty";
}

const plural = (count: number, one: string, many: string) => `${count.toLocaleString()} ${count === 1 ? one : many}`;

/** The box's headline: how many episodes of the language on screen it is holding back. */
export function holdingLine(bootstrap: SeriesBootstrap, lang: string): string {
  const held = bootstrap.held_by_lang[lang] ?? 0;
  return `Auto-request is holding ${plural(held, "episode", "episodes")} for the glossary`;
}

/** What was requested and what could not be: only episodes with a source subtitle count. */
export function requestedLine(bootstrap: SeriesBootstrap): string {
  const first = `Only the first ${plural(bootstrap.limit, "episode", "episodes")} with source subtitles ${bootstrap.limit === 1 ? "was" : "were"} requested`;
  return bootstrap.no_source > 0
    ? `${first} (${plural(bootstrap.no_source, "other episode has", "other episodes have")} no source subtitle yet).`
    : `${first}.`;
}

/** The sentence for the state the series is in. */
export function modeLine(mode: BootstrapMode, bootstrap: SeriesBootstrap): string {
  switch (mode) {
    case "review":
      return `${plural(bootstrap.pending, "suggestion waits", "suggestions wait")} for your review. Decide them and auto-request goes on by itself.`;
    case "running":
      return "The first episodes are being translated. Suggestions come once their facts are read.";
    case "memory-off":
      return "Story memory is off for this series, so it makes no suggestions by itself. Turn it on above, or write the glossary yourself.";
    case "empty":
      return "There are no suggestions to review. Add terms, characters or forms of address in the Glossary, or release auto-request without them.";
  }
}

/** The label of the button that opens the price: with an empty glossary it says so. */
export function releaseLabel(bootstrap: SeriesBootstrap, lang: string): string {
  const held = bootstrap.held_by_lang[lang] ?? 0;
  return bootstrap.glossary_empty ? `Auto-request ${held.toLocaleString()} without glossary…` : `Auto-request remaining ${held.toLocaleString()}…`;
}

/** The line that says what the day's cap leaves for this release; `null` when there is no cap. */
export function capLine(cap: number | undefined, left: number | null | undefined): string | null {
  if (!cap || cap <= 0) return null;
  return `up to ${(left ?? cap).toLocaleString()} today · daily cap ${cap.toLocaleString()}`;
}
