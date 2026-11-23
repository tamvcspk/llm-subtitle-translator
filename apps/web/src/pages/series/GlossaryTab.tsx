import { Alert, Stack } from "@mantine/core";
import { useOutletContext } from "react-router";
import { OwnerCharacters } from "../../components/Characters";
import { OwnerGlossary } from "../../components/OwnerGlossary";
import { ResearchTerms } from "../../components/ResearchTerms";
import type { SeriesContext } from "../Series";

const KEEPS_NOTHING = "The worker keeps nothing for this series yet. Translate an episode first, then its glossary can be set here.";

/** Glossary tab of a series (wireframe 2c): Terms and Forms of address, and **Research terms** above them (UX §6.7). */
export function SeriesGlossary() {
  const { page, lang } = useOutletContext<SeriesContext>();
  const storedId = page.series.series_id;
  if (storedId === null) return <Alert color="blue">{KEEPS_NOTHING}</Alert>;
  return (
    <Stack gap="lg">
      {lang !== "" && <ResearchTerms seriesId={storedId} lang={lang} />}
      <OwnerGlossary owner={{ kind: "series", id: storedId }} lang={lang} />
    </Stack>
  );
}

/** Characters tab of a series (wireframe 2d): Name and Voice of each character. */
export function SeriesCharacters() {
  const { page, lang } = useOutletContext<SeriesContext>();
  const storedId = page.series.series_id;
  if (storedId === null) return <Alert color="blue">{KEEPS_NOTHING.replace("glossary", "characters")}</Alert>;
  return <OwnerCharacters owner={{ kind: "series", id: storedId }} lang={lang} />;
}
