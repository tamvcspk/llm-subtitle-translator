import { Group, Stack, Table, Text } from "@mantine/core";
import type { InsightsDay } from "../../api/types";
import { barWidth, count, dayEditRate, money, percent } from "../../lib/insights";
import { useWide } from "./parts";

/** The bar of a day against the day of the window with the most episodes (no CDN, two divs). */
function Bar({ day, days }: { day: InsightsDay; days: InsightsDay[] }) {
  return (
    <div aria-hidden style={{ flex: 1, minWidth: 0, background: "var(--mantine-color-default-border)", borderRadius: 4, height: 8 }}>
      <div style={{ width: `${barWidth(day, days)}%`, height: 8, borderRadius: 4, background: "var(--mantine-primary-color-filled)" }} />
    </div>
  );
}

/** `12 eps · 0 failed · edit 3.8 % · 0.16`: what a day came to, in the words of the wireframe. */
function dayLine(day: InsightsDay, priced: boolean): string {
  const parts = [`${count(day.episodes)} eps`, `${count(day.failed)} failed`, `edit ${percent(dayEditRate(day))}`];
  if (priced) parts.push(money(day.cost));
  return parts.join(" · ");
}

export function Days({ days, priced }: { days: InsightsDay[]; priced: boolean }) {
  const wide = useWide();
  const newest = [...days].reverse();
  if (wide) {
    return (
      <Table.ScrollContainer minWidth={560}>
        <Table aria-label="Days" verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Date</Table.Th>
              <Table.Th w="40%">
                <Text span size="xs" c="dimmed" fw={400}>
                  bar = episodes that day
                </Text>
              </Table.Th>
              <Table.Th ta="right">Episodes</Table.Th>
              <Table.Th ta="right">Failed calls</Table.Th>
              <Table.Th ta="right">Cues you corrected</Table.Th>
              {priced && <Table.Th ta="right">Cost</Table.Th>}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {newest.map((day) => (
              <Table.Tr key={day.day}>
                <Table.Td>{day.day}</Table.Td>
                <Table.Td>
                  <Bar day={day} days={days} />
                </Table.Td>
                <Table.Td ta="right">{count(day.episodes)}</Table.Td>
                <Table.Td ta="right">{count(day.failed)}</Table.Td>
                <Table.Td ta="right">{percent(dayEditRate(day))}</Table.Td>
                {priced && <Table.Td ta="right">{money(day.cost)}</Table.Td>}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    );
  }
  return (
    <Stack gap="xs" component="ul" aria-label="Days" style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {newest.map((day) => (
        <Stack key={day.day} component="li" gap={2}>
          <Group wrap="nowrap" gap="sm" align="center" style={{ minHeight: 28 }}>
            <Text size="sm" w={86} style={{ flex: "none" }}>
              {day.day}
            </Text>
            <Bar day={day} days={days} />
          </Group>
          <Text size="xs" c="dimmed">
            {dayLine(day, priced)}
          </Text>
        </Stack>
      ))}
    </Stack>
  );
}
