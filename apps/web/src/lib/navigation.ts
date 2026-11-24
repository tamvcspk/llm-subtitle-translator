/**
 * Remember where the admin was in the Library area (`/library`, `/series/...`, `/unit/...`) and Home's scroll offset
 * across tab switches (P18e), stored in `sessionStorage` so navigating to Review / Activity and back never loses place.
 */

const LIBRARY_KEY = "gst.lastLibraryPath";
const HOME_SCROLL_KEY = "gst.homeScrollY";

export function isLibraryPath(pathname: string): boolean {
  return pathname === "/library" || pathname.startsWith("/series/") || pathname.startsWith("/unit/");
}

export function rememberLibraryPath(pathname: string, search: string): void {
  if (!isLibraryPath(pathname)) return;
  try {
    sessionStorage.setItem(LIBRARY_KEY, `${pathname}${search}`);
  } catch {
    // storage may be disabled in a restricted iframe
  }
}

export function lastLibraryPath(): string {
  try {
    const saved = sessionStorage.getItem(LIBRARY_KEY);
    if (saved && isLibraryPath(saved.split("?")[0] ?? "")) return saved;
  } catch {
    // ignore
  }
  return "/library";
}

export function saveHomeScroll(y: number): void {
  try {
    sessionStorage.setItem(HOME_SCROLL_KEY, String(Math.max(0, Math.round(y))));
  } catch {
    // ignore
  }
}

export function readHomeScroll(): number {
  try {
    const raw = sessionStorage.getItem(HOME_SCROLL_KEY);
    const n = raw ? Number(raw) : 0;
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch {
    return 0;
  }
}

export function clearNavigationMemory(): void {
  try {
    sessionStorage.removeItem(LIBRARY_KEY);
    sessionStorage.removeItem(HOME_SCROLL_KEY);
  } catch {
    // ignore
  }
}
