import { Alert, Anchor, Button, Card, Drawer, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useOutletContext } from "react-router";
import { useExtractFacts, useSeriesMemory } from "../../api/queries";
import type { MemoryUnit } from "../../api/types";
import { extractTarget, memoryIsOn, missingFacts, resultLine } from "../../lib/memory";
import type { SeriesContext } from "../Series";

/** The price of Extract facts (UX §6.4): one call per episode that has a source and no facts, counted here from the list. */
function ExtractSheet({ opened, units, onClose }: { opened: boolean; units: MemoryUnit[]; onClose: () => void }) {
  const extract = useExtractFacts();
  const missing = missingFacts(units);
  const target = extractTarget(units);

  const close = () => {
    extract.reset();
    onClose();
  };
  const failed = Object.values(extract.data?.results ?? {}).some((result) => result === "failed");

  return (
    <Drawer opened={opened} onClose={close} position="bottom" size="auto" title="Extract facts" padding="lg" radius="lg">
      <Stack>
        {extract.data ? (
          <>
            <Text>{failed ? "Some episodes could not be read." : "The facts are stored."}</Text>
            <Text size="sm" c="dimmed">
              {resultLine(extract.data.results)}
            </Text>
            <Button onClick={close}>Close</Button>
          </>
        ) : (
          <>
            <Group justify="space-between" wrap="nowrap">
              <Text c="dimmed">Episodes</Text>
              <Text fw={600}>{missing.length.toLocaleString()}</Text>
            </Group>
            <Group justify="space-between" wrap="nowrap">
              <Text c="dimmed">Model calls (est.)</Text>
              <Text fw={600}>{missing.length.toLocaleString()}</Text>
            </Group>
            <Text size="sm" c="dimmed">
              One call per episode, with the memory model. Nothing is translated.
            </Text>
            {extract.isError && <Alert color="red">{extract.error.message}</Alert>}
            <Group justify="space-between">
              <Button variant="default" onClick={close}>
                Cancel
              </Button>
              <Button disabled={target === null} loading={extract.isPending} onClick={() => target && extract.mutate(target.id)}>
                Confirm
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}

/** Context tab of a series (wireframe 2e): which episodes have facts, in story order, and **Extract facts** for the ones that do not. */
export function SeriesContextTab() {
  const { page, lang } = useOutletContext<SeriesContext>();
  const storedId = page.series.series_id;
  const memory = useSeriesMemory(storedId);
  const [asking, setAsking] = useState(false);

  if (storedId === null) {
    return <Alert color="blue">The worker keeps nothing for this series yet. Translate an episode first, then its story memory can be read here.</Alert>;
  }
  if (memory.isPending) return <Loader size="sm" aria-label="Loading story memory" />;
  if (memory.isError) return <Alert color="red">{memory.error.message}</Alert>;

  const { units } = memory.data;
  const missing = missingFacts(units);

  return (
    <Stack gap="md">
      {!memoryIsOn(memory.data) && (
        <Alert color="blue">
          Story memory is off for this series
          {memory.data.policy === "" ? " (it follows the global setting, which is off)" : ""}. Turn it on above to give later episodes the story so far.
        </Alert>
      )}

      <Card withBorder padding="md" radius="md" component="section" aria-label="Story memory">
        <Stack gap="sm">
          <Title order={4}>Story memory</Title>
          <Text>
            {missing.length === 0
              ? "Every episode with a source subtitle has its facts."
              : `${missing.length.toLocaleString()} ${missing.length === 1 ? "episode has" : "episodes have"} a source subtitle but no facts.`}
          </Text>
          <Group>
            <Button disabled={missing.length === 0} onClick={() => setAsking(true)}>
              {`Extract facts (${missing.length.toLocaleString()} ${missing.length === 1 ? "episode" : "episodes"})`}
            </Button>
          </Group>
        </Stack>
      </Card>

      {units.length === 0 ? (
        <Text c="dimmed">No episodes yet.</Text>
      ) : (
        <Stack gap={0} component="ul" aria-label="Episodes" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {units.map((unit, index) => (
            <Group
              component="li"
              key={unit.id}
              justify="space-between"
              wrap="nowrap"
              gap="sm"
              mih={44}
              style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
            >
              <Anchor component={Link} to={`/unit/${unit.id}/context${lang ? `?lang=${encodeURIComponent(lang)}` : ""}`}>
                {index + 1} · {unit.label}
              </Anchor>
              <Text size="sm" c="dimmed" aria-label={`Source ${unit.has_source ? "yes" : "no"}, facts ${unit.has_facts ? "yes" : "no"}`}>
                Source {unit.has_source ? "✓" : "✗"} · Facts {unit.has_facts ? "✓" : "✗"}
              </Text>
            </Group>
          ))}
        </Stack>
      )}

      <ExtractSheet opened={asking} units={units} onClose={() => setAsking(false)} />
    </Stack>
  );
}
