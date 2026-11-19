import { Alert, Anchor, Button, Card, Group, Loader, Modal, Stack, Tabs, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useCatalogStatus, useQueue, useStatus, useStopJob, useSyncCatalog, useTasks } from "../api/queries";
import type { QueueAnswer, QueueJob } from "../api/types";
import { EstimateSheet, type EstimateRequest } from "../components/EstimateSheet";
import { JobSpecSheet } from "../components/JobSpecSheet";
import { TaskRow } from "../components/TaskRow";
import {
  firstTab,
  isJobTab,
  JOB_TABS,
  jobName,
  jobsOf,
  jobStatus,
  lastLine,
  layerRows,
  providerLine,
  reach,
  type JobTab,
} from "../lib/activity";
import { ago } from "../lib/format";

function Providers({ queue }: { queue: QueueAnswer }) {
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label="Providers">
      <Stack gap="sm">
        <Title order={4}>Providers</Title>
        {queue.providers.length === 0 && <Text c="dimmed">No model is configured.</Text>}
        <Stack gap="sm" component="ul" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {queue.providers.map((provider) => {
            const line = providerLine(provider);
            return (
              <Stack key={provider.model} gap={0} component="li">
                <Text fw={600}>{provider.model}</Text>
                <Text size="sm" c={provider.usable ? "dimmed" : "orange"}>
                  {line.head}
                </Text>
                {line.detail && (
                  <Text size="sm" c="dimmed">
                    {line.detail}
                  </Text>
                )}
              </Stack>
            );
          })}
        </Stack>
        <Text size="xs" c="dimmed">
          Calls left today are shown in the price box.
        </Text>
      </Stack>
    </Card>
  );
}

/** The admin's own long requests (Extract facts, Look for proposals): what runs after a box was closed. Quiet when there are none. */
function BackgroundWork() {
  const tasks = useTasks();
  const items = tasks.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label="Background work">
      <Stack gap="sm">
        <Title order={4}>Background work</Title>
        <Text size="xs" c="dimmed">
          Your own requests, one at a time. A restart forgets this list; what was read stays stored.
        </Text>
        <Stack gap="md" component="ul" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {items.map((task) => (
            <li key={task.id}>
              <TaskRow task={task} />
            </li>
          ))}
        </Stack>
      </Stack>
    </Card>
  );
}

function Layers() {
  const status = useStatus();
  const layers = status.data?.model_layers;
  const rows = layerRows(layers);
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label="Model layers">
      <Stack gap="xs">
        <Title order={4}>Model layers</Title>
        {layers && (
          <Text size="xs" c="dimmed">
            Done {reach(layers.window.done_days)}, failed {reach(layers.window.failed_days)}. Read only.
          </Text>
        )}
        {status.isPending && <Loader size="sm" aria-label="Loading model layers" />}
        {layers && rows.length === 0 && <Text c="dimmed">No finished job yet.</Text>}
        {rows.map((row) => (
          <Group key={row.model} justify="space-between" wrap="nowrap">
            <Text>{row.model}</Text>
            <Text>
              done {row.done} · failed {row.failed}
            </Text>
          </Group>
        ))}
        {layers && layers.escalated.total > 0 && (
          <Text size="sm">
            Escalated {layers.escalated.total}: done {layers.escalated.done} · failed {layers.escalated.failed} · waiting {layers.escalated.waiting}
          </Text>
        )}
      </Stack>
    </Card>
  );
}

function Catalog() {
  const catalog = useCatalogStatus();
  const sync = useSyncCatalog();
  const [accepting, setAccepting] = useState(false);
  const data = catalog.data;
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label="Catalog">
      <Modal opened={accepting} onClose={() => setAccepting(false)} title="Accept a smaller library?" centered>
        <Stack gap="sm">
          <Text>
            The last read of Bazarr listed {data?.shrink?.listed.toLocaleString()} items,{" "}
            {data?.shrink?.dropped.toLocaleString()} fewer than before. If that is real, the worker will stop listing the
            missing ones (their subtitles and translations stay on disk). Nothing is deleted and nothing is translated.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAccepting(false)}>
              Cancel
            </Button>
            <Button
              loading={sync.isPending}
              onClick={() => {
                sync.mutate({ acceptShrink: true });
                setAccepting(false);
              }}
            >
              Read again and accept
            </Button>
          </Group>
        </Stack>
      </Modal>
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Stack gap={0}>
          <Title order={4}>Catalog</Title>
          {data ? (
            <>
              <Text>
                Synced {ago(data.synced_at)} · {data.status === "kept" ? "kept" : "ok"} · {data.items.toLocaleString()} items
              </Text>
              {data.warning && (
                <Text size="sm" c="orange">
                  {data.warning}
                </Text>
              )}
            </>
          ) : (
            <Loader size="sm" aria-label="Loading catalog" />
          )}
        </Stack>
        <Group gap="sm">
          {data?.shrink && (
            <Button variant="default" onClick={() => setAccepting(true)}>
              Accept smaller library
            </Button>
          )}
          <Button variant="default" loading={sync.isPending} onClick={() => sync.mutate(undefined)}>
            Sync now
          </Button>
        </Group>
      </Group>
      {sync.isError && (
        <Alert color="red" mt="sm">
          {sync.error.message}
        </Alert>
      )}
    </Card>
  );
}

function JobCard({
  job,
  tab,
  busy,
  onStop,
  onEstimate,
  onSpec,
}: {
  job: QueueJob;
  tab: JobTab;
  busy: boolean;
  onStop: (jobId: string) => void;
  onEstimate: (request: EstimateRequest) => void;
  onSpec: (job: QueueJob) => void;
}) {
  const name = jobName(job);
  const detail = tab === "failed" && job.error && job.error.trim().includes("\n") ? job.error.trim() : "";
  return (
    <Card withBorder padding="md" radius="md" component="li" style={{ listStyle: "none" }}>
      <Stack gap="xs">
        {typeof job.unit_id === "number" ? (
          <Anchor component={Link} to={`/unit/${job.unit_id}/subtitles${job.target_code ? `?lang=${encodeURIComponent(job.target_code)}` : ""}`} fw={600}>
            {name}
          </Anchor>
        ) : (
          <Text fw={600}>{name}</Text>
        )}
        <Text size="sm" c="dimmed">
          {jobStatus(job, tab)}
        </Text>
        {detail && (
          <details>
            <summary>Full error</summary>
            <Text size="xs" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {detail}
            </Text>
          </details>
        )}
        <Group gap="xs" wrap="wrap">
          {tab === "requested" && (
            <Button variant="default" color="red" loading={busy} aria-label={`Cancel ${name}`} onClick={() => onStop(job.job_id)}>
              Cancel
            </Button>
          )}
          {tab === "running" && (
            <Button variant="default" color="red" loading={busy} aria-label={`Stop ${name}`} onClick={() => onStop(job.job_id)}>
              Stop
            </Button>
          )}
          {tab === "waiting" && job.wait_reason === "context" && (
            <Button
              variant="light"
              aria-label={`Request that episode for ${name}`}
              onClick={() =>
                onEstimate({ action: `Request ${(job.missing_units ?? []).join(", ") || "the earlier episodes"}`, prerequisites: { job_id: job.job_id } })
              }
            >
              Request that episode
            </Button>
          )}
          {tab === "failed" && (
            <>
              <Button variant="light" aria-label={`Retry ${name}`} onClick={() => onEstimate({ action: `Retry · ${name}`, retry: { job_id: job.job_id } })}>
                Retry
              </Button>
              <Button variant="default" aria-label={`Export job spec of ${name}`} onClick={() => onSpec(job)}>
                Export job spec
              </Button>
            </>
          )}
        </Group>
      </Stack>
    </Card>
  );
}

const EMPTY: Record<JobTab, string> = {
  requested: "Nothing is waiting in line.",
  running: "Nothing is running.",
  waiting: "No job is waiting for anything.",
  failed: "No job has failed.",
  done: "No job has finished yet.",
};

function Jobs({ queue }: { queue: QueueAnswer }) {
  const [params, setParams] = useSearchParams();
  const asked = params.get("tab");
  const tab: JobTab = isJobTab(asked) ? asked : firstTab(queue.counts);
  const stop = useStopJob();
  const [estimate, setEstimate] = useState<EstimateRequest | null>(null);
  const [spec, setSpec] = useState<QueueJob | null>(null);

  const jobs = jobsOf(queue, tab);
  const count = queue.counts[JOB_TABS.find((candidate) => candidate.value === tab)?.state ?? "pending"];

  const choose = (value: string | null) => {
    if (value && isJobTab(value)) setParams({ tab: value }, { replace: true });
  };

  return (
    <Stack gap="sm" component="section" aria-label="Jobs">
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Stack gap={0}>
          <Title order={4}>Jobs</Title>
          <Text size="xs" c="dimmed">
            The latest 100 of each kind; the numbers on the tabs are real.
          </Text>
        </Stack>
        <Button
          variant="light"
          disabled={queue.counts.failed === 0}
          onClick={() => setEstimate({ action: "Retry all failed", retry: { all: true } })}
        >
          Retry all failed
        </Button>
      </Group>

      <Tabs value={tab} onChange={choose} keepMounted={false}>
        <Tabs.List style={{ overflowX: "auto", flexWrap: "nowrap" }}>
          {JOB_TABS.map((item) => (
            <Tabs.Tab key={item.value} value={item.value} style={{ minHeight: 44, flexShrink: 0 }}>
              {item.label} {queue.counts[item.state]}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>

      {stop.isError && <Alert color="red">{stop.error.message}</Alert>}
      {jobs.length === 0 ? (
        <Text c="dimmed">{EMPTY[tab]}</Text>
      ) : (
        <Stack gap="sm" component="ul" aria-label={`${JOB_TABS.find((item) => item.value === tab)?.label} jobs`} style={{ margin: 0, padding: 0 }}>
          {jobs.map((job) => (
            <JobCard
              key={job.job_id}
              job={job}
              tab={tab}
              busy={stop.isPending && stop.variables === job.job_id}
              onStop={(jobId) => stop.mutate(jobId)}
              onEstimate={setEstimate}
              onSpec={setSpec}
            />
          ))}
        </Stack>
      )}
      {count > jobs.length && (
        <Text size="sm" c="dimmed">
          Showing the latest {jobs.length} of {count.toLocaleString()}.
        </Text>
      )}

      <EstimateSheet request={estimate} onClose={() => setEstimate(null)} />
      <JobSpecSheet jobId={spec?.job_id ?? null} name={spec ? `${jobName(spec)}${spec.error ? ` · ${lastLine(spec.error)}` : ""}` : ""} onClose={() => setSpec(null)} />
    </Stack>
  );
}

/** Activity (wireframe 5): provider health, how the two model layers are doing, the catalog and every job by its state. */
export function Activity() {
  const queue = useQueue();
  return (
    <Stack gap="md">
      <Title order={2}>Activity</Title>
      {queue.isPending && <Loader size="sm" aria-label="Loading activity" />}
      {queue.isError && <Alert color="red">{queue.error.message}</Alert>}
      <BackgroundWork />
      {queue.data && <Providers queue={queue.data} />}
      {queue.data && <Layers />}
      <Catalog />
      {queue.data && <Jobs queue={queue.data} />}
    </Stack>
  );
}
