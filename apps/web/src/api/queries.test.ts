import { describe, expect, it } from "vitest";
import * as queries from "./queries";

/** Every runtime name the pages import from `api/queries` (the file was one module of 1200 lines before it was split by area). */
const NAMES = [
  "keys",
  // core, catalog
  "useStatus", "useQueue", "useJobSpec", "useMe", "useSignOut",
  "useCatalogStatus", "useSyncCatalog", "useCatalog", "useMovies", "useSeriesPage", "useSeriesUnits",
  "useSetAutoRequest", "useReleaseBootstrap", "useBootstrapEstimate", "useIgnore",
  // jobs, units, corrections
  "useEstimate", "useRequestJobs", "useStopJob",
  "useUnit", "useCues", "useUnitContext", "useVersions", "useDiff", "useCompare", "useAdoptImpact", "useChoose", "useRevert", "useAdopt", "useCueTotal",
  "useCorrections", "useEditCue", "useAddCorrection", "useRemoveCorrection", "useRemoveOutput",
  // style, glossary
  "useOwnerStyle", "useStyleSuggestions", "useStyleImpact", "useSaveStyle", "useEpisodeStyle", "usePreviewStyle",
  "useGlossary", "useGlossaryDryRun", "useSaveGlossary", "useGlossaryHistory", "useRestoreImpact", "useRestoreGlossary",
  "useCopyImpact", "useCopyGlossary", "useCardImpact", "useSaveCharacter", "useDeleteCharacter",
  // settings, memory
  "useSettings", "useSaveSettings", "useModels", "useLanguages", "useTestConnection", "useCleanup", "useChangePassword",
  "useBackups", "useCreateBackup", "useImportBackup", "useLogs", "useClearLogs",
  "useSeriesMemory", "useAdoptEpisodes", "useSaveMemory", "useExtractFacts", "openExtractJobs", "useExtractJobs", "useStopJobs", "useUnitFacts",
  // review, research, insights
  "useSuggestionSummary", "useSuggestions", "useSearchSuggestions", "useSuggestionImpact", "useBulkImpact",
  "useAcceptSuggestion", "useRejectSuggestion", "useUndoSuggestion", "useBulkAccept",
  "useResearchEstimate", "useResearchTerms",
  "useInsights", "useLibrarySeries", "fetchGolden", "useFromEdits",
];

describe("api/queries", () => {
  it("still exports every name the pages import, from the per-area files", () => {
    for (const name of NAMES) expect(queries, name).toHaveProperty(name);
  });

  it("keeps the refresh helpers inside", () => {
    expect(queries).not.toHaveProperty("useRefreshJobs");
    expect(queries).not.toHaveProperty("useRefreshUnit");
    expect(queries).not.toHaveProperty("useRefreshStyle");
  });
});
