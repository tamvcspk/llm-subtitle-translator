import { SimpleGrid, Text } from "@mantine/core";
import type { Insights } from "../../api/types";
import { count, percent, reviewLabel, trend } from "../../lib/insights";
import { GRID, Section, Stat } from "./parts";

/** What you had to fix, and what the review found, with the change against the window before. */
export function QualitySection({ data, days }: { data: Insights; days: number }) {
  return (
    <Section
      title="Quality"
      note={`What you had to fix, and what the review found, over the last ${days} days. ▼ is better and ▲ worse than the ${days} days before.`}
    >
      <SimpleGrid cols={GRID}>
        <Stat
          label="Cues you corrected"
          value={percent(data.quality.cue_edits.rate)}
          trend={trend(data.quality.cue_edits.rate, data.previous.cue_edit_rate)}
          hint={`${count(data.quality.cue_edits.cues)} of ${count(data.quality.cues_translated)} cues translated`}
        />
        <Stat
          label="Repairs"
          value={percent(data.quality.repair.rate)}
          trend={trend(data.quality.repair.rate, data.previous.repair_rate)}
          hint={`${count(data.quality.repair.calls)} repair or split calls of ${count(data.quality.repair.translate_calls)}`}
        />
        <Stat
          label="Retranslated"
          value={percent(data.quality.retranslations.rate)}
          trend={trend(data.quality.retranslations.rate, data.previous.retranslate_rate)}
          hint={`${count(data.quality.retranslations.count)} of ${count(data.quality.episodes)} episodes; ${count(data.quality.retranslations.reverts)} reverted`}
        />
        <Stat
          label="Read too fast"
          value={percent(data.quality.review.cps_rate)}
          trend={trend(data.quality.review.cps_rate, data.previous.cps_rate)}
          hint={`${count(data.quality.review.cps)} cues over 20 characters a second`}
        />
        <Stat
          label="Lines too long"
          value={percent(data.quality.review.too_long_rate)}
          trend={trend(data.quality.review.too_long_rate, data.previous.too_long_rate)}
          hint={`${count(data.quality.review.too_long)} cues over 42 characters a line`}
        />
      </SimpleGrid>
      {Object.keys(data.quality.review.by_kind).length > 0 && (
        <Text size="sm" c="dimmed">
          Review found: {Object.entries(data.quality.review.by_kind).map(([kind, n]) => `${reviewLabel(kind)} ${count(n)}`).join(" · ")}
        </Text>
      )}
    </Section>
  );
}
