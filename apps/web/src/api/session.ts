import { useSyncExternalStore } from "react";

/** Where the console puts the open `#hash` before it leaves for /login, and where /login reads it back. */
export const RETURN_KEY = "gst-return";

type Listener = () => void;

let expired = false;
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener();
}

/** Any `401` from `/api/*` means the session is gone (UX §7). The page stays; an overlay blocks it. */
export function markSessionExpired(): void {
  if (expired) return;
  expired = true;
  emit();
}

export function resetSession(): void {
  if (!expired) return;
  expired = false;
  emit();
}

export function useSessionExpired(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => expired,
    () => false,
  );
}

/** Only a fragment of this page is ever stored: the sign-in page can then send the browser back to `/` + it. */
export function isSafeReturn(value: string | null | undefined): value is string {
  return typeof value === "string" && value.length <= 512 && /^#[\w\-./?=&%:~,+@!$'()*;]*$/.test(value);
}

export function rememberReturn(hash: string, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  if (!storage || !isSafeReturn(hash)) return;
  try {
    storage.setItem(RETURN_KEY, hash);
  } catch {
    // Private mode or a full quota: signing in still works, only the way back is lost.
  }
}

function safeStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * Sign in again: leave for /login and come back to the same `#hash`. This reloads the page, so a form that
 * keeps a draft (UX §7) must have put it in sessionStorage before this runs; the overlay itself blocks the page
 * and does not reload anything until the person presses Sign in.
 */
export function goToLogin(): void {
  rememberReturn(window.location.hash);
  window.location.assign("/login");
}
