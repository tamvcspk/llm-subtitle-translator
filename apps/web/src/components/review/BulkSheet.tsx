import { Alert, Button, Drawer, Group, Loader, Stack, Text } from "@mantine/core";
import { useBulkAccept, useBulkImpact } from "../../api/queries";
import type { BulkAnswer } from "../../api/types";
import { impactLine, pendingLine } from "../../lib/suggestions";

/**
 * "Accept all in <series>": one series, the number it says is the number the worker will check (`expect`), and what it
 * would flag stale is the worker's own count for all of them together. Nothing is retranslated.
 */
export function BulkSheet({
  group,
  onDone,
  onClose,
}: {
  group: { seriesId: number; series: string; count: number } | null;
  onDone: (answer: BulkAnswer, series: string) => void;
  onClose: () => void;
}) {
  const accept = useBulkAccept();
  const impact = useBulkImpact(group?.seriesId ?? 0, group !== null && !accept.isPending && !accept.isSuccess);
  const close = () => {
    accept.reset();
    onClose();
  };
  const answer = impact.data;
  return (
    <Drawer opened={group !== null} onClose={close} position="bottom" size="auto" title={group ? `Accept all · ${group.series}` : ""} padding="lg" radius="lg">
      <Stack>
        {group && (
          <Text>
            Accept {pendingLine(group.count)} for {group.series}? They are written to its glossary and character cards.
          </Text>
        )}
        {impact.isPending && group !== null && (
          <Group gap="xs" role="status">
            <Loader size="sm" aria-label="Counting" />
            <Text size="sm" c="dimmed">
              Counting affected translations…
            </Text>
          </Group>
        )}
        {impact.isError && <Alert color="red">{impact.error.message}</Alert>}
        {answer && (
          <Text size="sm" c="dimmed" role="status">
            {impactLine(answer.would_stale)}
          </Text>
        )}
        {answer && group && answer.count !== group.count && (
          <Alert color="orange">The list has changed: {pendingLine(answer.count)} are waiting now. Close this and look again.</Alert>
        )}
        {accept.isPending && (
          <Group gap="xs" role="status">
            <Loader size="sm" aria-label="Applying" />
            <Text size="sm" c="dimmed">
              Applying suggestions and counting affected translations…
            </Text>
          </Group>
        )}
        {accept.isError && <Alert color="red">{accept.error.message}</Alert>}
        <Group justify="space-between">
          <Button variant="default" onClick={close}>
            Cancel
          </Button>
          <Button
            disabled={!answer || !group || answer.count !== group.count || answer.count === 0}
            loading={accept.isPending}
            onClick={() => group && accept.mutate({ seriesId: group.seriesId, expect: group.count }, { onSuccess: (result) => onDone(result, group.series) })}
          >
            {group ? `Accept ${group.count}` : "Accept"}
          </Button>
        </Group>
      </Stack>
    </Drawer>
  );
}
