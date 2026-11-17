import { Alert, Button, Group, SegmentedControl, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { useSaveMemory, useSeriesMemory } from "../api/queries";
import type { SeriesMemory } from "../api/types";
import { anchorProblem, fromChoice, POLICY_CHOICES, toChoice } from "../lib/memory";

function Controls({ storedId, memory }: { storedId: number; memory: SeriesMemory }) {
  const [policy, setPolicy] = useState(toChoice(memory.policy));
  const [anchor, setAnchor] = useState(memory.specials_after);
  const save = useSaveMemory(storedId);
  const problem = anchorProblem(anchor);
  const dirty = fromChoice(policy) !== memory.policy || anchor.trim().toUpperCase() !== memory.specials_after;

  return (
    <Stack gap="xs">
      <Group align="flex-end" gap="lg" wrap="wrap">
        <Stack gap={4}>
          <Text size="sm" fw={500} id="story-memory-label">
            Story memory
          </Text>
          <SegmentedControl aria-labelledby="story-memory-label" data={POLICY_CHOICES} value={policy} onChange={setPolicy} />
        </Stack>
        <TextInput
          label="Specials after"
          description="Where season 0 sits in the story"
          placeholder="After the last episode of season 1"
          value={anchor}
          error={problem}
          onChange={(event) => setAnchor(event.currentTarget.value)}
          style={{ flex: "1 1 14rem", maxWidth: 320 }}
        />
      </Group>
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Text size="sm" c="dimmed">
          Changing Story memory or Specials after flags translations Stale. Existing translations are never redone.
        </Text>
        {dirty && (
          <Group gap="xs">
            <Button
              variant="default"
              onClick={() => {
                setPolicy(toChoice(memory.policy));
                setAnchor(memory.specials_after);
                save.reset();
              }}
            >
              Discard
            </Button>
            <Button
              disabled={problem !== null}
              loading={save.isPending}
              onClick={() => save.mutate({ policy: fromChoice(policy), specials_after: anchor.trim().toUpperCase() })}
            >
              Apply
            </Button>
          </Group>
        )}
      </Group>
      {save.isError && <Alert color="red">{save.error.message}</Alert>}
    </Stack>
  );
}

/**
 * Story memory and Specials after in the header of a series (wireframe 2): saved with Apply because changing either can flag
 * translations stale. There is no impact line (the API cannot count it yet, UX §6.2). It starts again from what the worker
 * says after every save.
 */
export function MemoryControls({ storedId }: { storedId: number }) {
  const memory = useSeriesMemory(storedId);
  if (memory.isPending) return null;
  if (memory.isError) {
    return (
      <Text size="sm" c="dimmed">
        Story memory is not available for this series.
      </Text>
    );
  }
  return <Controls key={`${memory.data.policy}|${memory.data.specials_after}`} storedId={storedId} memory={memory.data} />;
}
