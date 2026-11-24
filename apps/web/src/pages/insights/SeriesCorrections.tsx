import { Alert, Anchor, Badge, Button, Card, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router";
import { useFromEdits, useSeriesUnits } from "../../api/queries";
import type { CorrectionCount, LibrarySeriesRow } from "../../api/types";
import type { GoldenTarget } from "../../components/GoldenSheet";
import { count, editsOf, fromEditsLine } from "../../lib/insights";

/** The golden set and the look for repeated corrections of one series, one row per language it has. */
export function SeriesCorrections({
  series,
  href,
  counts,
  onGolden,
}: {
  series: LibrarySeriesRow;
  href: string | null;
  counts: readonly CorrectionCount[];
  onGolden: (target: GoldenTarget) => void;
}) {
  const languages = useSeriesUnits(series.id).data?.languages ?? [];
  const look = useFromEdits();
  const [said, setSaid] = useState<{ lang: string; line: string } | null>(null);
  if (languages.length === 0) return null;
  return (
    <Card withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }} aria-label={series.title}>
      <Stack gap="xs">
        {href ? (
          <Anchor component={Link} to={href} fw={600}>
            {series.title}
          </Anchor>
        ) : (
          <Text fw={600}>{series.title}</Text>
        )}
        {languages.map((lang) => {
          const edits = editsOf(counts, series.id, lang);
          return (
          <Group key={lang} gap="sm" wrap="wrap">
            <Badge variant="light" size="lg" radius="sm" tt="none">
              {`${lang} · ${count(edits)} edits`}
            </Badge>
            <Button
              variant="default"
              disabled={edits === 0}
              loading={look.isPending && look.variables?.lang === lang && look.variables.seriesId === series.id}
              onClick={() =>
                look.mutate({ seriesId: series.id, lang }, { onSuccess: (answer) => setSaid({ lang, line: fromEditsLine(answer) }) })
              }
              aria-label={`Look at my ${lang} edits of ${series.title}`}
            >
              Look at my edits
            </Button>
            <Button
              variant="default"
              disabled={edits === 0}
              onClick={() => onGolden({ seriesId: series.id, seriesKey: series.key, title: series.title, lang })}
              aria-label={`Open the ${lang} golden set of ${series.title}`}
            >
              Golden set
            </Button>
          </Group>
          );
        })}
        {said && <Text size="sm" role="status">{`${said.lang}: ${said.line}`}</Text>}
        {look.isError && <Alert color="red">{look.error.message}</Alert>}
      </Stack>
    </Card>
  );
}
