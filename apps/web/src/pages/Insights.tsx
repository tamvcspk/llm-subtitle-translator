import { Alert, Badge, Button, Group, Loader, SegmentedControl, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useCatalog, useInsights, useLibrarySeries } from "../api/queries";
import { GoldenSheet, type GoldenTarget } from "../components/GoldenSheet";
import { AB_ENOUGH, DAY_CHOICES, editsOf } from "../lib/insights";
import { seriesRoute } from "../lib/seriesLink";
import { Days } from "./insights/Days";
import { QueueSection, ReliabilitySection } from "./insights/Operations";
import { Pairs } from "./insights/Pairs";
import { QualitySection } from "./insights/Quality";
import { SeriesCorrections } from "./insights/SeriesCorrections";
import { StyleTrials } from "./insights/StyleTrials";
import { Section } from "./insights/parts";

const CORRECTIONS_ID = "your-corrections";

/**
 * Insights (FR-19.5, UX A.6b, wireframe 5b): how the worker is doing and how good its translations are, worked out from its own
 * records. The figure that matters most is the share of cues you had to correct, by model and prompt version: measure before you
 * change a prompt or a style, then again after.
 */
export function Insights() {
  const [days, setDays] = useState<number>(30);
  const [golden, setGolden] = useState<GoldenTarget | null>(null);
  const insights = useInsights(days);
  const series = useLibrarySeries();
  const catalog = useCatalog();
  const data = insights.data;
  const priced = (data?.operations.tokens.cost ?? 0) > 0;
  const corrections = data?.corrections ?? [];

  return (
    <Stack gap="md">
      <Stack gap={2}>
        <Group justify="space-between" align="center" wrap="wrap" gap="xs">
          <Title order={2}>Insights</Title>
          <Badge variant="light" color="gray" size="lg" radius="sm" tt="none">
            read-only · no model calls
          </Badge>
        </Group>
        <Text c="dimmed">Worked out from what the worker recorded. Nothing here costs a model call.</Text>
      </Stack>
      <Group justify="space-between" align="center" wrap="wrap" gap="sm">
        <SegmentedControl
          aria-label="Window"
          value={String(days)}
          onChange={(value) => setDays(Number(value))}
          data={DAY_CHOICES.map((choice) => ({ value: String(choice), label: `${choice}d` }))}
          styles={{ label: { minHeight: 44, display: "flex", alignItems: "center" } }}
        />
        <Button
          variant="subtle"
          onClick={() => document.getElementById(CORRECTIONS_ID)?.scrollIntoView({ behavior: "smooth", block: "start" })}
        >
          Your corrections ↓
        </Button>
      </Group>

      {insights.isPending && <Loader size="sm" aria-label="Loading the figures" />}
      {insights.isError && <Alert color="red">{insights.error.message}</Alert>}

      {data && (
        <>
          <QualitySection data={data} days={days} />

          <Section
            title="By model and prompt version"
            note="Measure before you change a prompt or a style, then again after: a model you corrected more often is the weaker one for your shows, and the prompt version that wrote a cue is blamed for your correction."
          >
            <Pairs rows={data.by_model_prompt} />
          </Section>

          <Section
            title="Style A/B scores"
            note={`Your verdicts on variants, added up by style and prompt version. A win counts one, a tie a half. Under ${AB_ENOUGH} comparisons a score is not a conclusion.`}
          >
            <StyleTrials rows={data.ab} />
          </Section>

          <QueueSection data={data} priced={priced} />

          <ReliabilitySection data={data} />

          <Section title="Daily" note="Episodes translated per day, UTC; a full bar is the busiest day of the window.">
            <Days days={data.daily} priced={priced} />
          </Section>
        </>
      )}

      <Section
        id={CORRECTIONS_ID}
        title="Your corrections"
        note="Find repeated edit patterns to propose to Review, or open a golden set: the cues you corrected, to measure a prompt or a style against with subcore compare --golden. Looking costs nothing."
      >
        {series.isPending && <Loader size="sm" aria-label="Loading the series" />}
        {series.data && series.data.items.length === 0 && <Text c="dimmed">The worker keeps no series yet.</Text>}
        <SimpleGrid cols={{ base: 1, md: 2 }} component="ul" aria-label="Series" style={{ margin: 0, padding: 0 }}>
          {[...(series.data?.items ?? [])]
            .sort((a, b) => editsOf(corrections, b.id) - editsOf(corrections, a.id) || a.title.localeCompare(b.title))
            .map((row) => (
              <SeriesCorrections key={row.id} series={row} href={seriesRoute(catalog.data, row.id, "glossary")} counts={corrections} onGolden={setGolden} />
            ))}
        </SimpleGrid>
      </Section>

      <GoldenSheet target={golden} onClose={() => setGolden(null)} />
    </Stack>
  );
}
