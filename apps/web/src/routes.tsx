import { Navigate, Route, Routes } from "react-router";
import { Placeholder } from "./pages/Placeholder";
import { Shell } from "./shell/Shell";

/** Hash routes (`#/activity`): the console runs in an iframe of Organizr and must survive a reload. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Placeholder title="Library" note="Series and movies from Bazarr: next item of P12b." />} />
        <Route path="activity" element={<Placeholder title="Activity" note="Queue, providers and quota: later in P12b." />} />
        <Route path="settings" element={<Placeholder title="Settings" note="Seven groups, saved one by one: later in P12b." />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
