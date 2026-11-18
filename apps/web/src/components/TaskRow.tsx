import { Alert, Button, Group, Progress, Stack, Text } from "@mantine/core";
import { useCancelTask } from "../api/queries";
import type { BackgroundTask } from "../api/types";
import { progressLine } from "../lib/memory";

/** One background task (a long request of the admin): where it is, how far, and Stop while it can still be stopped. */
export function TaskRow({ task, showTitle = true }: { task: BackgroundTask; showTitle?: boolean }) {
  const cancel = useCancelTask();
  const active = task.state === "queued" || task.state === "running";
  const percent = task.total > 0 ? Math.round((task.done / task.total) * 100) : 0;
  return (
    <Stack gap="xs">
      <Group justify="space-between" wrap="nowrap" align="flex-start" gap="sm">
        <Stack gap={0}>
          {showTitle && <Text fw={600}>{task.title}</Text>}
          <Text size="sm" c={task.state === "failed" ? "red" : "dimmed"}>
            {task.cancel_requested && active ? "Stopping at the next step" : progressLine(task)}
          </Text>
        </Stack>
        {active && (
          <Button
            variant="default"
            color="red"
            loading={cancel.isPending}
            disabled={task.cancel_requested}
            aria-label={`Stop ${task.title}`}
            onClick={() => cancel.mutate(task.id)}
          >
            Stop
          </Button>
        )}
      </Group>
      {active && (
        <Progress
          value={task.state === "queued" ? 0 : percent}
          animated={task.state === "running"}
          aria-label={`Progress of ${task.title}`}
        />
      )}
      {task.error && <Alert color="red">{task.error}</Alert>}
      {cancel.isError && <Alert color="red">{cancel.error.message}</Alert>}
    </Stack>
  );
}
