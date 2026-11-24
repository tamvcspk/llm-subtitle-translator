import { Card, SimpleGrid, Stack, Table, Text } from "@mantine/core";
import type { InsightsBreakdown } from "../../api/types";
import { count, percent } from "../../lib/insights";
import { Grid, Stat, useWide } from "./parts";

/** `gemini-3.1-flash-lite · prompt v4`: a model with the prompt version it wrote with. */
function pairName(row: InsightsBreakdown): string {
  const model = row.model || "(model not recorded)";
  return row.prompt_version ? `${model} · prompt ${row.prompt_version}` : model;
}

const pairKey = (row: InsightsBreakdown) => `${row.model ?? ""}|${row.prompt_version ?? ""}`;

/** One model with one prompt version, as a card (below 1024 px; the table of the same figures is `PairTable`). */
function PairCard({ row }: { row: InsightsBreakdown }) {
  return (
    <Card withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
      <Stack gap="xs">
        <Text fw={600}>{pairName(row)}</Text>
        <SimpleGrid cols={{ base: 2, md: 5 }} spacing="xs">
          <Stat label="Episodes" value={count(row.episodes)} hint={`${count(row.cues)} cues`} />
          <Stat label="Cues you corrected" value={percent(row.cue_edit_rate)} hint={`${count(row.cue_edits)} cues`} />
          <Stat label="Repairs" value={percent(row.repair_rate)} hint="of translate calls" />
          <Stat label="Read too fast" value={percent(row.cps_rate)} />
          <Stat label="Lines too long" value={percent(row.too_long_rate)} />
        </SimpleGrid>
      </Stack>
    </Card>
  );
}

function PairTable({ rows }: { rows: InsightsBreakdown[] }) {
  return (
    <Grid label="By model and prompt version" head={["Model", "Prompt", "Episodes", "Cues you corrected", "Repairs", "Read too fast", "Lines too long"]}>
      {rows.map((row) => (
        <Table.Tr key={pairKey(row)}>
          <Table.Td fw={600}>{row.model || "(not recorded)"}</Table.Td>
          <Table.Td ta="right">{row.prompt_version || "—"}</Table.Td>
          <Table.Td ta="right">{count(row.episodes)}</Table.Td>
          <Table.Td ta="right">{`${percent(row.cue_edit_rate)} (${count(row.cue_edits)})`}</Table.Td>
          <Table.Td ta="right">{percent(row.repair_rate)}</Table.Td>
          <Table.Td ta="right">{percent(row.cps_rate)}</Table.Td>
          <Table.Td ta="right">{percent(row.too_long_rate)}</Table.Td>
        </Table.Tr>
      ))}
    </Grid>
  );
}

export function Pairs({ rows }: { rows: InsightsBreakdown[] }) {
  const wide = useWide();
  if (rows.length === 0) return <Text c="dimmed">Nothing has been translated in this window.</Text>;
  if (wide) return <PairTable rows={rows} />;
  return (
    <Stack gap="sm" component="ul" aria-label="By model and prompt version" style={{ margin: 0, padding: 0 }}>
      {rows.map((row) => (
        <PairCard key={pairKey(row)} row={row} />
      ))}
    </Stack>
  );
}
