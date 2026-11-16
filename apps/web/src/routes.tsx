import { Navigate, Route, Routes } from "react-router";
import { Episodes } from "./pages/Episodes";
import { Home } from "./pages/Home";
import { Placeholder } from "./pages/Placeholder";
import { Series } from "./pages/Series";
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
          <Route path="style" element={<Placeholder title="Style" note="Presets, Try 20 lines and the impact line: next in P12b." />} />
          <Route path="glossary" element={<Placeholder title="Glossary" note="Terms, forms of address and history: later in P12b." />} />
          <Route path="characters" element={<Placeholder title="Characters" note="Character cards: later in P12b." />} />
          <Route path="context" element={<Placeholder title="Context" note="Story memory, Specials after and Extract facts: later in P12b." />} />
          <Route path="*" element={<Navigate to="episodes" replace />} />
        </Route>
        <Route path="unit/:id/*" element={<Placeholder title="Unit" note="Subtitles, Corrections, Retranslate, History and Style: next in P12b." />} />
        <Route path="activity" element={<Placeholder title="Activity" note="Queue, providers and quota: later in P12b." />} />
        <Route path="settings" element={<Placeholder title="Settings" note="Seven groups, saved one by one: later in P12b." />} />
        <Route path="*" element={<Navigate to="/library" replace />} />
      </Route>
    </Routes>
  );
}
