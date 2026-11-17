import { Navigate, Route, Routes } from "react-router";
import { Episodes } from "./pages/Episodes";
import { Compare } from "./pages/Compare";
import { Home } from "./pages/Home";
import { MovieGlossary } from "./pages/MovieGlossary";
import { Placeholder } from "./pages/Placeholder";
import { Series } from "./pages/Series";
import { SeriesContextTab } from "./pages/series/ContextTab";
import { SeriesCharacters, SeriesGlossary } from "./pages/series/GlossaryTab";
import { SeriesStyle } from "./pages/series/StyleTab";
import { Corrections } from "./pages/unit/Corrections";
import { UnitStyle } from "./pages/unit/StyleTab";
import { Context } from "./pages/unit/Context";
import { History } from "./pages/unit/History";
import { Retranslate } from "./pages/unit/Retranslate";
import { Subtitles } from "./pages/unit/Subtitles";
import { Unit } from "./pages/Unit";
import { Shell } from "./shell/Shell";

/** Hash routes (`#/activity`): the console runs in an iframe of Organizr and must survive a reload. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/library" replace />} />
        <Route path="library" element={<Home />} />
        <Route path="series/:id" element={<Series />}>
          <Route index element={<Navigate to="episodes" replace />} />
          <Route path="episodes" element={<Episodes />} />
          <Route path="style" element={<SeriesStyle />} />
          <Route path="glossary" element={<SeriesGlossary />} />
          <Route path="characters" element={<SeriesCharacters />} />
          <Route path="context" element={<SeriesContextTab />} />
          <Route path="*" element={<Navigate to="episodes" replace />} />
        </Route>
        <Route path="unit/:id/compare/:version" element={<Compare />} />
        <Route path="unit/:id/glossary" element={<MovieGlossary />} />
        <Route path="unit/:id" element={<Unit />}>
          <Route index element={<Navigate to="subtitles" replace />} />
          <Route path="subtitles" element={<Subtitles />} />
          <Route path="corrections" element={<Corrections />} />
          <Route path="retranslate" element={<Retranslate />} />
          <Route path="history" element={<History />} />
          <Route path="context" element={<Context />} />
          <Route path="style" element={<UnitStyle />} />
          <Route path="*" element={<Navigate to="subtitles" replace />} />
        </Route>
        <Route path="activity" element={<Placeholder title="Activity" note="Queue, providers and quota: later in P12b." />} />
        <Route path="settings" element={<Placeholder title="Settings" note="Seven groups, saved one by one: later in P12b." />} />
        <Route path="*" element={<Navigate to="/library" replace />} />
      </Route>
    </Routes>
  );
}
