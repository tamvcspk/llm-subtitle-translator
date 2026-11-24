

export const keys = {
  unit: (id: number) => ["unit", id] as const,
  series: (id: number) => ["catalog", "series", id] as const,
  seriesUnits: (storedId: number) => ["library", "series", storedId, "units"] as const,
  status: ["status"] as const,
  catalogStatus: ["catalog", "status"] as const,
  me: ["auth", "me"] as const,
  queue: ["queue"] as const,
  catalog: ["catalog", "overview"] as const,
  movies: ["catalog", "movies"] as const,
};
