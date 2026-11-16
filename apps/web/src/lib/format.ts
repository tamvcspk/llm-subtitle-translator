/** "2m ago" for a Unix-seconds timestamp, as the status strip shows catalog age (UX §4.2). */
export function ago(unixSeconds: number | null | undefined, nowMs: number = Date.now()): string {
  if (!unixSeconds) return "never";
  const seconds = Math.max(0, Math.round(nowMs / 1000 - unixSeconds));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** "14:05" in the viewer's zone, for `until` and `retry_at`. */
export function clock(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
