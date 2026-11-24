import { Alert } from "@mantine/core";
import { useOutletContext } from "react-router";
import { ReviewInbox } from "../Review";
import type { SeriesContext } from "../Series";

/** Suggestions tab on the Series page (P18e): Review inbox filtered to this series so the admin never loses place. */
export function SeriesSuggestions() {
  const { page } = useOutletContext<SeriesContext>();
  const storedId = page.series.series_id;
  if (storedId === null) {
    return (
      <Alert color="blue">
        The worker keeps nothing for this series yet. Translate an episode or extract facts first, then proposals appear here.
      </Alert>
    );
  }
  return <ReviewInbox seriesId={storedId} embedded />;
}
