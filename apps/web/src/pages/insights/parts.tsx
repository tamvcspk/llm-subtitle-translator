import { Card, Stack, Table, Text, Title } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import type { ReactNode } from "react";
import type { Trend } from "../../lib/insights";

/** From this width the lists of figures are tables, below it cards (wireframe 5b, UX §8). */
export function useWide(): boolean {
  return useMediaQuery("(min-width: 1024px)") ?? false;
}

export function Section({ title, note, id, children }: { title: string; note?: string; id?: string; children: ReactNode }) {
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label={title} id={id}>
      <Stack gap="sm">
        <Stack gap={2}>
          <Title order={3} size="h4">
            {title}
          </Title>
          {note && (
            <Text size="sm" c="dimmed">
              {note}
            </Text>
          )}
        </Stack>
        {children}
      </Stack>
    </Card>
  );
}

const TREND_COLOR = { better: "teal", worse: "red", same: "dimmed", none: "dimmed" } as const;

export function Stat({ label, value, hint, trend }: { label: string; value: ReactNode; hint?: ReactNode; trend?: Trend | null }) {
  return (
    <Stack gap={2} role="group" aria-label={label}>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text fw={700} size="xl">
        {value}
      </Text>
      {trend && (
        <Text size="xs" c={TREND_COLOR[trend.tone]} fw={trend.tone === "better" || trend.tone === "worse" ? 600 : undefined}>
          {trend.text}
        </Text>
      )}
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Stack>
  );
}

export const GRID = { base: 1, xs: 2, md: 3 } as const;

/** A table that scrolls on its own when it is wider than the page, never the page. */
export function Grid({ label, head, children }: { label: string; head: string[]; children: ReactNode }) {
  return (
    <Table.ScrollContainer minWidth={560}>
      <Table aria-label={label} verticalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            {head.map((name, i) => (
              <Table.Th key={name} ta={i === 0 ? "left" : "right"}>
                {name}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>{children}</Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
