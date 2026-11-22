import type { CatalogOverview } from "../api/types";

/**
 * The page of a series is addressed by the catalog's id (`/series/<catalog id>/...`), not by the worker's own series id
 * that Library, Review and Insights carry. Returns the route of `tab` for the series the worker knows as `storedId`, or
 * `null` when the catalog has no row for it (not loaded yet, or Bazarr never listed it): the caller then shows plain text.
 */
export function seriesRoute(catalog: CatalogOverview | undefined, storedId: number, tab: string): string | null {
  const row = catalog?.series.find((candidate) => candidate.series_id === storedId);
  return row ? `/series/${row.id}/${tab}` : null;
}
