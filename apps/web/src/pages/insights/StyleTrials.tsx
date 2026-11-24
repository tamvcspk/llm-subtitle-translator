import { Group, Stack, Table, Text } from "@mantine/core";
import type { InsightsAb } from "../../api/types";
import { abLabel, AB_ENOUGH, count, percent, tooFewToConclude } from "../../lib/insights";
import { Grid, useWide } from "./parts";

/** One style and prompt version: what the admin chose between the variant and the current. Dimmed until it means something. */
function AbRow({ row }: { row: InsightsAb }) {
  const few = tooFewToConclude(row);
  return (
    <Stack gap={2} component="li" style={{ listStyle: "none" }}>
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Text fw={600}>{abLabel(row)}</Text>
        <Text size="sm" c={few ? "dimmed" : undefined} fw={few ? undefined : 700}>
          Score {percent(row.score)} · {count(row.variant)} for the variant, {count(row.current)} for the current, {count(row.tie)} ties
        </Text>
      </Group>
      {few && (
        <Text size="sm" c="dimmed">
          {`Too few comparisons to conclude (${count(row.judged)} of ${AB_ENOUGH} needed)`}
        </Text>
      )}
    </Stack>
  );
}

function AbTable({ rows }: { rows: InsightsAb[] }) {
  return (
    <Grid label="Style trials" head={["Style", "Compared", "Variant", "Current", "Ties", "Score"]}>
      {rows.map((row) => {
        const few = tooFewToConclude(row);
        return (
          <Table.Tr key={`${row.style}|${row.prompt_version}`}>
            <Table.Td fw={600}>
              {abLabel(row)}
              {few && (
                <Text size="xs" c="dimmed">
                  {`Too few comparisons to conclude (needs ${AB_ENOUGH})`}
                </Text>
              )}
            </Table.Td>
            <Table.Td ta="right">{count(row.judged)}</Table.Td>
            <Table.Td ta="right">{count(row.variant)}</Table.Td>
            <Table.Td ta="right">{count(row.current)}</Table.Td>
            <Table.Td ta="right">{count(row.tie)}</Table.Td>
            <Table.Td ta="right" c={few ? "dimmed" : undefined} fw={few ? undefined : 700}>
              {percent(row.score)}
            </Table.Td>
          </Table.Tr>
        );
      })}
    </Grid>
  );
}

export function StyleTrials({ rows }: { rows: InsightsAb[] }) {
  const wide = useWide();
  if (rows.length === 0) return <Text c="dimmed">No variant has been judged cue by cue yet.</Text>;
  if (wide) return <AbTable rows={rows} />;
  return (
    <Stack gap="sm" component="ul" aria-label="Style trials" style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {rows.map((row) => (
        <AbRow key={`${row.style}|${row.prompt_version}`} row={row} />
      ))}
    </Stack>
  );
}
