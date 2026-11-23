import { Tabs } from "@mantine/core";
import { useLocation, useNavigate, useSearchParams } from "react-router";

export interface TabSpec {
  value: string;
  label: string;
}

/**
 * Tabs that are routes (`#/series/1/style`): the one under `base` that the address names is the active one, and a tap goes
 * there. Only the parameters named in `keep` travel along (the target language yes, a filter no).
 */
export function RouteTabs({ base, tabs, keep = [] }: { base: string; tabs: readonly TabSpec[]; keep?: readonly string[] }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const rest = location.pathname.startsWith(`${base}/`) ? location.pathname.slice(base.length + 1).split("/")[0] : "";
  const active = tabs.find((tab) => tab.value === rest)?.value ?? tabs[0]?.value ?? "";

  const go = (value: string | null) => {
    if (!value) return;
    const carried = new URLSearchParams();
    for (const name of keep) {
      const found = params.get(name);
      if (found) carried.set(name, found);
    }
    const search = carried.toString();
    void navigate({ pathname: `${base}/${value}`, search: search ? `?${search}` : "" });
  };

  return (
    <Tabs value={active} onChange={go} keepMounted={false}>
      <Tabs.List style={{ overflowX: "auto", flexWrap: "nowrap" }}>
        {tabs.map((tab) => (
          // Tighter on a phone so the five tabs of a series fit at 390 px; a page with more tabs (an episode) still scrolls.
          <Tabs.Tab key={tab.value} value={tab.value} px={{ base: 6, xs: "md" }} style={{ minHeight: 44, flexShrink: 0 }}>
            {tab.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Tabs>
  );
}
