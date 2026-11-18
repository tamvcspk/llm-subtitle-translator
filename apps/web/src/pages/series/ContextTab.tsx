import { Alert, Anchor, Button, Card, Checkbox, Collapse, Drawer, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useOutletContext } from "react-router";
import { useExtractFacts, useSeriesMemory, useTasks, useUnitFacts } from "../../api/queries";
import type { MemoryUnit, UnitFacts } from "../../api/types";
import { FactsView } from "../../components/FactsView";
import { TaskRow } from "../../components/TaskRow";
import { memoryIsOn, missingFacts, resultLine, taskResults } from "../../lib/memory";
import type { SeriesContext } from "../Series";

/**
 * The price of Extract facts (UX §6.4): one call per episode asked for, counted here from the list. The work runs in the
 * background: **Confirm** stays to watch it, **Run in background** closes the box and Activity keeps showing it.
 */
function ExtractSheet({ opened, ids, onClose }: { opened: boolean; ids: number[]; onClose: () => void }) {
  const extract = useExtractFacts();
  const tasks = useTasks();
  const [taskId, setTaskId] = useState<string | null>(null);
  const task = taskId === null ? undefined : tasks.data?.items.find((item) => item.id === taskId);
  const active = task !== undefined && (task.state === "queued" || task.state === "running");

  const close = () => {
    extract.reset();
    setTaskId(null);
    onClose();
  };
  const start = (thenClose: boolean) => {
    const anchor = ids.at(-1);
    if (anchor === undefined) return;
    extract.mutate(
      { anchor, unitIds: ids },
      {
        onSuccess: (answer) => {
          if (thenClose) close();
          else setTaskId(answer.task.id);
        },
      },
    );
  };

  const results = task ? taskResults(task) : {};
  const failed = Object.values(results).some((result) => result === "failed");

  return (
    <Drawer opened={opened} onClose={close} position="bottom" size="auto" title="Extract facts" padding="lg" radius="lg">
      <Stack>
        {taskId !== null ? (
          <>
            {task === undefined ? <Loader size="sm" aria-label="Starting" /> : <TaskRow task={task} showTitle={false} />}
            {task?.state === "done" && (
              <>
                <Text>{failed ? "Some episodes could not be read." : "The facts are stored."}</Text>
                <Text size="sm" c="dimmed">
                  {resultLine(results)}
                </Text>
              </>
            )}
            <Button onClick={close}>{active || task === undefined ? "Run in background" : "Close"}</Button>
          </>
        ) : (
          <>
            <Group justify="space-between" wrap="nowrap">
              <Text c="dimmed">Episodes</Text>
              <Text fw={600}>{ids.length.toLocaleString()}</Text>
            </Group>
            <Group justify="space-between" wrap="nowrap">
              <Text c="dimmed">Model calls (est.)</Text>
              <Text fw={600}>{ids.length.toLocaleString()}</Text>
            </Group>
            <Text size="sm" c="dimmed">
              One call per episode, with the memory model. Nothing is translated. You can close this box: the work goes on and Activity shows it.
            </Text>
            {extract.isError && <Alert color="red">{extract.error.message}</Alert>}
            <Group justify="space-between" wrap="wrap">
              <Button variant="default" onClick={close}>
                Cancel
              </Button>
              <Group gap="xs" wrap="wrap">
                <Button variant="light" disabled={ids.length === 0} loading={extract.isPending} onClick={() => start(true)}>
                  Run in background
                </Button>
                <Button disabled={ids.length === 0} loading={extract.isPending} onClick={() => start(false)}>
                  Confirm
                </Button>
              </Group>
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}

function FactsPanel({ unit }: { unit: MemoryUnit }) {
  const facts = useUnitFacts(unit.id, true);
  if (facts.isPending) return <Loader size="sm" aria-label={`Loading the facts of ${unit.label}`} />;
  if (facts.isError) return <Alert color="red">{facts.error.message}</Alert>;
  const data: UnitFacts = facts.data;
  return <FactsView facts={data} />;
}

/** One episode: tick it to read its facts, open it to see what they say. */
function EpisodeRow({
  unit,
  index,
  lang,
  picked,
  onPick,
}: {
  unit: MemoryUnit;
  index: number;
  lang: string;
  picked: boolean;
  onPick: (picked: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const readable = unit.has_source && !unit.has_facts;
  return (
    <Stack
      component="li"
      gap="xs"
      py={4}
      style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
    >
      <Group justify="space-between" wrap="nowrap" gap="sm" mih={44}>
        <Group gap="sm" wrap="nowrap">
          <Checkbox
            size="md"
            checked={picked}
            disabled={!readable}
            onChange={(event) => onPick(event.currentTarget.checked)}
            aria-label={`Read the facts of ${unit.label}`}
          />
          <Anchor component={Link} to={`/unit/${unit.id}/context${lang ? `?lang=${encodeURIComponent(lang)}` : ""}`}>
            {index + 1} · {unit.label}
          </Anchor>
        </Group>
        <Group gap="sm" wrap="nowrap">
          <Text size="sm" c="dimmed" aria-label={`Source ${unit.has_source ? "yes" : "no"}, facts ${unit.has_facts ? "yes" : "no"}`}>
            Source {unit.has_source ? "✓" : "✗"} · Facts {unit.has_facts ? "✓" : "✗"}
          </Text>
          {unit.has_facts && (
            <Button
              variant="subtle"
              size="compact-md"
              mih={44}
              aria-expanded={open}
              aria-label={`${open ? "Hide" : "Show"} the facts of ${unit.label}`}
              onClick={() => setOpen(!open)}
            >
              {open ? "Hide" : "Show"}
            </Button>
          )}
        </Group>
      </Group>
      {unit.has_facts && (
        <Collapse expanded={open}>{open && <FactsPanel unit={unit} />}</Collapse>
      )}
    </Stack>
  );
}

/** Context tab of a series (wireframe 2e): which episodes have facts, in story order, what the facts say, and **Extract facts**. */
export function SeriesContextTab() {
  const { page, lang } = useOutletContext<SeriesContext>();
  const storedId = page.series.series_id;
  const memory = useSeriesMemory(storedId);
  const tasks = useTasks();
  const [asking, setAsking] = useState<number[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());

  if (storedId === null) {
    return <Alert color="blue">The worker keeps nothing for this series yet. Translate an episode first, then its story memory can be read here.</Alert>;
  }
  if (memory.isPending) return <Loader size="sm" aria-label="Loading story memory" />;
  if (memory.isError) return <Alert color="red">{memory.error.message}</Alert>;

  const { units } = memory.data;
  const missing = missingFacts(units);
  const readable = new Set(missing.map((unit) => unit.id));
  // A pick that was read meanwhile (or whose subtitle went) drops out by itself.
  const chosen = units.filter((unit) => picked.has(unit.id) && readable.has(unit.id)).map((unit) => unit.id);
  const running = (tasks.data?.items ?? []).filter(
    (task) => task.kind === "extract-facts" && task.series_id === storedId && (task.state === "queued" || task.state === "running"),
  );

  const toggle = (id: number, on: boolean) =>
    setPicked((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

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
          {running.map((task) => (
            <TaskRow key={task.id} task={task} />
          ))}
          <Group>
            <Button disabled={missing.length === 0} onClick={() => setAsking(missing.map((unit) => unit.id))}>
              {`Extract facts (${missing.length.toLocaleString()} ${missing.length === 1 ? "episode" : "episodes"})`}
            </Button>
            {chosen.length > 0 && (
              <>
                <Button variant="light" onClick={() => setAsking(chosen)}>
                  {`Extract ticked (${chosen.length.toLocaleString()})`}
                </Button>
                <Button variant="subtle" onClick={() => setPicked(new Set())}>
                  Clear ticks
                </Button>
              </>
            )}
          </Group>
          {missing.length > 0 && chosen.length === 0 && (
            <Text size="xs" c="dimmed">
              Tick the episodes to read, or read every one that lacks facts.
            </Text>
          )}
        </Stack>
      </Card>

      {units.length === 0 ? (
        <Text c="dimmed">No episodes yet.</Text>
      ) : (
        <Stack gap={0} component="ul" aria-label="Episodes" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {units.map((unit, index) => (
            <EpisodeRow
              key={unit.id}
              unit={unit}
              index={index}
              lang={lang}
              picked={picked.has(unit.id) && readable.has(unit.id)}
              onPick={(on) => toggle(unit.id, on)}
            />
          ))}
        </Stack>
      )}

      <ExtractSheet opened={asking !== null} ids={asking ?? []} onClose={() => setAsking(null)} />
    </Stack>
  );
}
