import { Alert, Button, Drawer, Group, Loader, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router";
import { useCleanup } from "../../api/queries";
import { formatBytes } from "../../lib/settings";

const GROUPS: Record<string, string> = {
  jobs_done: "Finished jobs",
  jobs_failed: "Failed jobs",
  versions: "Stored versions",
  glossary: "Glossary history",
  temp: "Temporary files",
};

/**
 * The part of Maintenance that is not a setting: **Clean up now** (a preview first, then a confirmation; media folders are
 * never touched) and the way to Backups and Logs.
 */
export function MaintenanceTools() {
  const [open, setOpen] = useState(false);
  const cleanup = useCleanup();
  const answer = cleanup.data;
  const done = answer !== undefined && !answer.dry_run;

  const close = () => {
    cleanup.reset();
    setOpen(false);
  };

  return (
    <>
      <Group gap="sm" wrap="wrap">
        <Button
          variant="default"
          onClick={() => {
            setOpen(true);
            cleanup.mutate(true);
          }}
        >
          Clean up now
        </Button>
        <Button component={Link} to="/backups" variant="default">
          Backups →
        </Button>
        <Button component={Link} to="/logs" variant="default">
          Logs →
        </Button>
      </Group>

      <Drawer opened={open} onClose={close} position="bottom" size="auto" title="Clean up now" padding="lg" radius="lg">
        <Stack>
          {cleanup.isPending && <Loader size="sm" aria-label="Working" />}
          {cleanup.isError && <Alert color="red">{cleanup.error.message}</Alert>}
          {answer && (
            <>
              <Text>
                {done ? "Removed" : "Preview: removes"} {answer.total.files.toLocaleString()} files and {answer.total.rows.toLocaleString()} database
                rows, {done ? "freeing" : "and frees"} {formatBytes(answer.total.bytes)}.
              </Text>
              <Stack gap={2}>
                {answer.groups
                  .filter((group) => group.enabled && (group.files > 0 || group.rows > 0 || group.error))
                  .map((group) => (
                    <Text key={group.name} size="sm" c={group.error ? "red" : "dimmed"}>
                      {GROUPS[group.name] ?? group.name}: {(group.files + group.rows).toLocaleString()} ({formatBytes(group.bytes)})
                      {group.error ? ` · ${group.error}` : ""}
                    </Text>
                  ))}
              </Stack>
              <Text size="sm" c="dimmed">
                Media folders are never touched.
              </Text>
            </>
          )}
          <Group justify="space-between">
            <Button variant="default" onClick={close}>
              {done ? "Close" : "Cancel"}
            </Button>
            {!done && (
              <Button color="red" disabled={!answer?.dry_run || cleanup.isPending} onClick={() => cleanup.mutate(false)}>
                Confirm
              </Button>
            )}
          </Group>
        </Stack>
      </Drawer>
    </>
  );
}
