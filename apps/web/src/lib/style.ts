import type { Style, StyleImpact } from "../api/types";

/** A style as the form holds it: the owner's preset (series and movies only) and the axes it sets itself. */
export interface StyleDraft {
  preset: string;
  style: Style;
}

type OptionKey = "formality" | "profanity" | "localization" | "dialect" | "honorifics" | "allow_condense";

export interface Axis {
  key: OptionKey;
  label: string;
  options: Array<{ value: string; label: string }>;
  /** Behind "More": the four main choices stay in front (UX §6.1). */
  more: boolean;
}

// The names on screen are the ones of UX §6.1; the values are the API's.
export const AXES: Axis[] = [
  {
    key: "formality",
    label: "Formality",
    more: false,
    options: [
      { value: "casual", label: "Casual" },
      { value: "neutral", label: "Neutral" },
      { value: "formal", label: "Formal" },
    ],
  },
  {
    key: "profanity",
    label: "Swearing",
    more: false,
    options: [
      { value: "keep", label: "Keep" },
      { value: "soften", label: "Soften" },
      { value: "remove", label: "Remove" },
    ],
  },
  {
    key: "localization",
    label: "Idioms and wordplay",
    more: false,
    options: [
      { value: "literal", label: "Literal" },
      { value: "balanced", label: "Balanced" },
      { value: "adapt", label: "Adapt" },
    ],
  },
  {
    key: "dialect",
    label: "Dialect",
    more: true,
    options: [
      { value: "north", label: "North" },
      { value: "south", label: "South" },
      { value: "neutral", label: "Neutral" },
    ],
  },
  {
    key: "honorifics",
    label: "Honorifics (anime)",
    more: true,
    options: [
      { value: "keep", label: "Keep" },
      { value: "translate", label: "Translate" },
    ],
  },
  {
    key: "allow_condense",
    label: "Fast dialogue",
    more: true,
    options: [
      { value: "allow", label: "Allow" },
      { value: "dont", label: "Don't" },
    ],
  },
];

export const NUMBER_FIELDS = [
  { key: "max_cps", label: "Characters per second" },
  { key: "max_line_length", label: "Longest line" },
  { key: "max_lines", label: "Lines per cue" },
] as const;

export const INHERIT = "inherit";

/** The value of the segmented control for an axis: `inherit` when the style leaves it out. */
export function axisValue(axis: Axis, style: Style): string {
  const value = style[axis.key];
  if (value === undefined) return INHERIT;
  if (axis.key === "allow_condense") return value ? "allow" : "dont";
  return String(value);
}

export function setAxis(style: Style, axis: Axis, value: string): Style {
  const next: Style = { ...style };
  if (value === INHERIT) delete next[axis.key];
  else if (axis.key === "allow_condense") next.allow_condense = value === "allow";
  else (next as Record<string, unknown>)[axis.key] = value;
  return next;
}

/** `Inherit · now Neutral`: what the axis is while the form leaves it out. */
export function inheritLabel(axis: Axis, effective: Style): string {
  const value = axisValue(axis, effective);
  const shown = axis.options.find((option) => option.value === value)?.label;
  return shown ? `Inherit · now ${shown}` : "Inherit";
}

/** Blank fields and axes left to inherit are not stored: two drafts are the same when what they would store is. */
export function cleanStyle(style: Style): Style {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(style)) {
    if (value === undefined || value === null) continue;
    if (typeof value === "string") {
      const text = value.trim();
      if (text !== "") out[key] = text;
    } else out[key] = value;
  }
  return out as Style;
}

export function cleanDraft(draft: StyleDraft): StyleDraft {
  return { preset: draft.preset, style: cleanStyle(draft.style) };
}

export function sameDraft(a: StyleDraft, b: StyleDraft): boolean {
  return JSON.stringify(sortKeys(cleanDraft(a))) === JSON.stringify(sortKeys(cleanDraft(b)));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, inner]) => [key, sortKeys(inner)]),
    );
  }
  return value;
}

/** "Saving flags 12 episodes stale." or "No translation is affected."; `null` while it is still being worked out. */
export function impactSentence(impact: Pick<StyleImpact, "would_stale"> | undefined, noun: "episode" | "movie" = "episode"): string | null {
  if (!impact) return null;
  const n = impact.would_stale;
  if (n === 0) return "No translation is affected.";
  return `Saving flags ${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"} stale.`;
}
