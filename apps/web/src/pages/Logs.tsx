import { Alert, Anchor, Button, Card, Drawer, Group, Loader, SegmentedControl, Stack, Text, TextInput, Title } from "@mantine/core";
import { IconChevronLeft } from "@tabler/icons-react";
import { useState } from "react";
import { Link } from "react-router";
import { useClearLogs, useLogs } from "../api/queries";
import { filterLogs, levelColor, LEVELS, logTime, type LevelFilter } from "../lib/logs";

const CHOICES = [{ value: "ALL", label: "All" }, ...LEVELS.map((level) => ({ value: level, label: level }))];

/** Logs (wireframe 7b): the tail of the worker's own log, filtered here. Older lines are not shown. */
export function Logs() {
  const logs = useLogs();
  const clear = useClearLogs();
  const [level, setLevel] = useState<LevelFilter>("ALL");
  const [search, setSearch] = useState("");
  const [asking, setAsking] = useState(false);

  const shown = filterLogs(logs.data?.entries ?? [], level, search);

  return (
    <Stack gap="md">
      <Stack gap="xs">
        <Anchor component={Link} to="/settings" size="sm" style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
          <IconChevronLeft size={16} /> Settings
        </Anchor>
        <Title order={2}>Logs</Title>
      </Stack>

      <Stack gap="sm">
        <Stack gap={4}>
          <Text size="sm" fw={500} id="log-level-label">
            Level
          </Text>
          <div style={{ overflowX: "auto" }}>
            <SegmentedControl aria-labelledby="log-level-label" data={CHOICES} value={level} onChange={(value) => setLevel(value as LevelFilter)} />
          </div>
        </Stack>
        <Group align="flex-end" wrap="wrap" gap="sm">
          <TextInput
            label="Search"
            placeholder="Search…"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            style={{ flex: "1 1 14rem" }}
          />
          <Button variant="default" loading={logs.isFetching && !logs.isPending} onClick={() => void logs.refetch()}>
            Refresh
          </Button>
          <Button variant="default" color="red" onClick={() => setAsking(true)}>
            Clear logs
          </Button>
        </Group>
      </Stack>

      {logs.isPending && <Loader size="sm" aria-label="Loading logs" />}
      {logs.isError && <Alert color="red">{logs.error.message}</Alert>}
      {logs.data && logs.data.truncated && (
        <Text size="sm" c="dimmed">
          Only the latest lines are shown; older ones are in the log file.
        </Text>
      )}
      {logs.data && shown.length === 0 && <Text c="dimmed">{logs.data.entries.length === 0 ? "The log is empty." : "No line matches."}</Text>}
      {shown.length > 0 && (
        <Card withBorder padding="xs" radius="md">
          <Stack gap={2} component="ul" aria-label="Log lines" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {shown.map((entry, index) => (
              <li key={`${entry.timestamp}-${index}`} style={{ fontFamily: "monospace", fontSize: "0.8rem", overflowWrap: "anywhere", padding: "4px 6px" }}>
                <Text component="span" c="dimmed" size="xs" ff="monospace">
                  {logTime(entry.timestamp)}
                </Text>{" "}
                <Text component="span" c={levelColor(entry.level)} fw={600} size="xs" ff="monospace">
                  {entry.level}
                </Text>{" "}
                <span>{entry.message}</span>
                {entry.details.trim() !== "" && (
                  <details>
                    <summary>Trace</summary>
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{entry.details.trimEnd()}</pre>
                  </details>
                )}
              </li>
            ))}
          </Stack>
        </Card>
      )}

      <Drawer opened={asking} onClose={() => setAsking(false)} position="bottom" size="auto" title="Clear logs?" padding="lg" radius="lg">
        <Stack>
          <Text>This empties the current log file. Older, rotated logs are kept.</Text>
          {clear.isError && <Alert color="red">{clear.error.message}</Alert>}
          <Group justify="space-between">
            <Button variant="default" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button color="red" loading={clear.isPending} onClick={() => clear.mutate(undefined, { onSuccess: () => setAsking(false) })}>
              Confirm clear
            </Button>
          </Group>
        </Stack>
      </Drawer>
    </Stack>
  );
}
