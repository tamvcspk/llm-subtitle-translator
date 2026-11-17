import { useCallback, useEffect, useState } from "react";

const PREFIX = "gst-draft:";

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null; // private mode or blocked site data: the draft then lives only on the page
  }
}

function read(id: string): Record<string, unknown> | null {
  try {
    const text = storage()?.getItem(PREFIX + id);
    const value: unknown = text ? JSON.parse(text) : null;
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function write(id: string, value: Record<string, unknown> | null): void {
  try {
    if (value === null) storage()?.removeItem(PREFIX + id);
    else storage()?.setItem(PREFIX + id, JSON.stringify(value));
  } catch {
    // A full quota only loses the way back after signing in again.
  }
}

export interface Draft<T extends Record<string, unknown>> {
  draft: T;
  set: (patch: Partial<T>) => void;
  /** Back to what the worker has; also forgets the kept copy. */
  reset: () => void;
  /** The draft came from an earlier visit (signing in again reloads the page), not from the worker. */
  restored: boolean;
}

/**
 * The values of a form, kept in `sessionStorage` while they differ from what the worker has (UX §7): signing in again
 * reloads the page, and a person who was halfway through a form must find it as they left it. Never put a secret in
 * `stored` or in a patch: a key typed in a box is not kept anywhere.
 *
 * `same` says whether a draft is the stored one, so a draft that was put back by hand is forgotten, not kept.
 * The caller mounts the form again (a `key`) when what the worker has changes.
 */
export function useDraft<T extends Record<string, unknown>>(id: string, stored: T, same: (a: T, b: T) => boolean): Draft<T> {
  const [state, setState] = useState<{ draft: T; restored: boolean }>(() => {
    const kept = read(id);
    if (kept) {
      const merged = { ...stored, ...kept } as T;
      if (!same(merged, stored)) return { draft: merged, restored: true };
    }
    return { draft: stored, restored: false };
  });

  // The caller gives `stored` and `same` stable (memoized): they are what the kept copy is judged against.
  useEffect(() => {
    write(id, same(state.draft, stored) ? null : state.draft);
  }, [id, state.draft, stored, same]);

  const set = useCallback((patch: Partial<T>) => setState((previous) => ({ draft: { ...previous.draft, ...patch }, restored: previous.restored })), []);
  const reset = useCallback(() => {
    write(id, null);
    setState({ draft: stored, restored: false });
  }, [id, stored]);

  return { draft: state.draft, set, reset, restored: state.restored };
}
