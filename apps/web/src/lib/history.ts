import type { Version } from "../api/types";
import { ago } from "./format";

export type VersionAction = "diff" | "compare" | "use" | "restore";

/** The buttons of one line of History (wireframe 3d), by what the version is. */
export function versionActions(version: Version): VersionAction[] {
  if (version.status === "pruned") return [];
  const out: VersionAction[] = [];
  if (version.parent_version !== null) out.push("diff");
  if (version.status === "candidate") {
    if (version.trigger === "variant") out.push("compare");
    out.push("use");
  } else if (version.status === "superseded" || version.status === "removed") {
    out.push("restore");
  }
  return out;
}

/** `retranslate · machine · superseded`, then what else says how it was made. */
export function versionHeadline(version: Version): string {
  return [version.trigger, version.origin, version.status].join(" · ");
}

export function versionDetails(version: Version, nowMs: number = Date.now()): string {
  const range = version.params.range ? `cues ${version.params.range.first}–${version.params.range.last}` : "";
  const tokens = version.tokens_out ? `${version.tokens_out.toLocaleString()} tokens out` : "";
  return [version.model, tokens, range, ago(version.created_at, nowMs)].filter(Boolean).join(" · ");
}

/** Why a line has no buttons, when that is not obvious. */
export function versionNote(version: Version): string | null {
  if (version.status === "pruned") return "The stored copy was removed by clean-up: it cannot be restored or compared.";
  if (version.status === "removed") return "You removed this subtitle. Restoring it writes the file back.";
  return null;
}
