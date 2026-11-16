import { Navigate, Route, Routes } from "react-router";
import { Home } from "./pages/Home";
import { Placeholder } from "./pages/Placeholder";
import { Shell } from "./shell/Shell";

/** Hash routes (`#/activity`): the console runs in an iframe of Organizr and must survive a reload. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/library" replace />} />
        <Route path="library" element={<Home />} />
        <Route path="series/:id/*" element={<Placeholder title="Series" note="Episodes, Style, Glossary, Characters and Context: next in P12b." />} />
        <Route path="unit/:id/*" element={<Placeholder title="Unit" note="Subtitles, Corrections, Retranslate, History and Style: next in P12b." />} />
        <Route path="activity" element={<Placeholder title="Activity" note="Queue, providers and quota: later in P12b." />} />
        <Route path="settings" element={<Placeholder title="Settings" note="Seven groups, saved one by one: later in P12b." />} />
        <Route path="*" element={<Navigate to="/library" replace />} />
      </Route>
    </Routes>
  );
}
