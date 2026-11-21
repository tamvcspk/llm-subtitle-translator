import { Alert } from "@mantine/core";
import { useOutletContext } from "react-router";
import { OwnerStyle } from "../../components/OwnerStyle";
import { episodeCode } from "../../lib/episodes";
import type { SeriesContext } from "../Series";

/** Style tab of a series (wireframe 2b). The sample episodes are the ones with a source subtitle and a unit the worker keeps. */
export function SeriesStyle() {
  const { page, lang } = useOutletContext<SeriesContext>();
  const storedId = page.series.series_id;
  if (storedId === null) {
    return <Alert color="blue">The worker keeps nothing for this series yet. Translate an episode first, then its style can be set here.</Alert>;
  }
  const episodes = page.items
    .filter((item) => item.has_source && item.unit_id !== null && !item.gone)
    .map((item) => ({ unit_id: item.unit_id as number, label: [episodeCode(item.season, item.episode), item.title].filter(Boolean).join(" · ") }));
  return <OwnerStyle owner={{ kind: "series", id: storedId }} lang={lang} episodes={episodes} />;
}
