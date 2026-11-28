import { Alert, Button, Checkbox, Drawer, Group, Loader, Stack, Text } from "@mantine/core";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useBootstrapEstimate, useReleaseBootstrap } from "../api/queries";
import type { SeriesBootstrap } from "../api/types";
import { bootstrapMode, capLine, holdingLine, modeLine, releaseJobs, releaseLabel, requestedLine } from "../lib/bootstrap";
import { estimateLines } from "./EstimateSheet";

function Line({ label, value }: { label: string; value: string }) {
  return (
    <Group justify="space-between" wrap="nowrap">
      <Text c="dimmed">{label}</Text>
      <Text fw={600}>{value}</Text>
    </Group>
  );
}

/**
 * The price of letting a held series go (UX 2): the episodes it would ask for, the calls and their models, the day's cap, and
 * an orange warning when nothing is approved. **Confirm** lets it go and asks for the rest at once, for this series only.
 */
function ReleaseSheet({
  seriesId,
  title,
  bootstrap,
  opened,
  onClose,
}: {
  seriesId: number;
  title: string;
  bootstrap: SeriesBootstrap;
  opened: boolean;
  onClose: () => void;
}) {
  const estimate = useBootstrapEstimate(seriesId, opened);
  const release = useReleaseBootstrap(seriesId);
  const answer = estimate.data;
  const done = release.data;

  const close = () => {
    release.reset();
    onClose();
  };
  const cap = answer ? capLine(answer.daily_cap, answer.cap_left) : null;
  const left = answer?.skipped_by_reason ? Object.entries(answer.skipped_by_reason).map(([reason, n]) => `${n} ${reason}`) : [];
  const jobs = answer?.estimate?.jobs ?? 0;
  const { label: jobsLabel, spread } = releaseJobs(bootstrap);
  const rows = answer?.estimate ? estimateLines(answer.estimate, { jobsLabel }) : [];
  if (spread) rows.splice(1, 0, ["By language", spread]);

  return (
    <Drawer opened={opened} onClose={close} position="bottom" size="auto" title={`Auto-request remaining episodes · ${title}`} padding="lg" radius="lg">
      <Stack>
        {done ? (
          <>
            <Text>
              {done.started === false
                ? "Released. Starts with the next library sync (a few minutes)."
                : "Released. The rest is being asked for now; follow it in Activity."}
            </Text>
            <Button onClick={close}>Close</Button>
          </>
        ) : (
          <>
            {estimate.isPending && <Loader size="sm" aria-label="Estimating" />}
            {estimate.isError && <Alert color="red">{estimate.error.message}</Alert>}
            {answer?.estimate && (
              <Stack gap="xs">
                {rows.map(([label, value]) => (
                  <Line key={label} label={label} value={value} />
                ))}
                {cap && (
                  <Text size="sm" c="dimmed">
                    {cap}
                  </Text>
                )}
                {jobs === 0 && <Text c="dimmed">Nothing is held back that could be requested now.</Text>}
                {left.length > 0 && (
                  <Text size="sm" c="dimmed">
                    {`Left out: ${left.join(", ")}.`}
                  </Text>
                )}
              </Stack>
            )}
            {answer?.glossary_empty && (
              <Alert color="orange" title="Glossary is empty">
                No terms, characters, or forms of address are approved yet. Character names and pronouns may vary across seasons.
              </Alert>
            )}
            {release.isError && <Alert color="red">{release.error.message}</Alert>}
            <Group justify="space-between" wrap="wrap">
              <Button variant="default" onClick={close}>
                Cancel
              </Button>
              <Button disabled={!answer} loading={release.isPending} onClick={() => release.mutate({ released: true })}>
                {jobs > 0 ? `Release & auto-request (${jobs.toLocaleString()})` : "Release"}
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}

/** "Hold again": what it pulls back, and the choice to leave the jobs already in line alone (they are the admin's to stop). */
function HoldSheet({
  seriesId,
  title,
  queued,
  opened,
  onClose,
}: {
  seriesId: number;
  title: string;
  queued: number;
  opened: boolean;
  onClose: () => void;
}) {
  const hold = useReleaseBootstrap(seriesId);
  const [cancel, setCancel] = useState(true);
  const close = () => {
    hold.reset();
    onClose();
  };
  return (
    <Drawer opened={opened} onClose={close} position="bottom" size="auto" title={`Hold auto-request for ${title}?`} padding="lg" radius="lg">
      <Stack>
        <Text>Auto-request will stop queueing further episodes until the glossary is set.</Text>
        <Checkbox
          checked={cancel}
          onChange={(event) => setCancel(event.currentTarget.checked)}
          label={`Also cancel ${queued.toLocaleString()} queued auto-request ${queued === 1 ? "job" : "jobs"}`}
          description="Running jobs finish their current batch; jobs you asked for are untouched. Letting it go again asks for these once more."
          styles={{ root: { minHeight: 44, display: "flex", alignItems: "center" } }}
        />
        {hold.isError && <Alert color="red">{hold.error.message}</Alert>}
        <Group justify="space-between" wrap="wrap">
          <Button variant="default" onClick={close}>
            Cancel
          </Button>
          <Button
            loading={hold.isPending}
            onClick={() => hold.mutate({ released: false, cancelQueuedPolicy: cancel }, { onSuccess: close })}
          >
            Hold again
          </Button>
        </Group>
      </Stack>
    </Drawer>
  );
}

/**
 * The two notices of the Bootstrap of `auto_request` on a series page (FR-15.12): the yellow box while the series is held,
 * and, after it was let go with nothing approved, the strip that holds it again.
 */
export function BootstrapNotice({ seriesId, title, bootstrap, lang }: { seriesId: number; title: string; bootstrap: SeriesBootstrap; lang: string }) {
  const [releasing, setReleasing] = useState(false);
  const [holding, setHolding] = useState(false);
  const hold = useReleaseBootstrap(seriesId);

  let notice: ReactNode = null;
  if (bootstrap.locked) {
    const mode = bootstrapMode(bootstrap);
    notice = (
      <Alert color="yellow" title={holdingLine(bootstrap, lang)} role="status">
        <Stack gap="xs">
          <Text size="sm">{requestedLine(bootstrap)}</Text>
          <Text size="sm">{modeLine(mode, bootstrap)} You can still translate any episode yourself.</Text>
          <Group gap="xs" wrap="wrap">
            {mode === "review" && (
              <Button component={Link} to={`/series/${seriesId}/suggestions`} variant="light">
                {`Review suggestions (${bootstrap.pending.toLocaleString()})`}
              </Button>
            )}
            {mode === "running" && (
              <Button component={Link} to="/activity" variant="light">
                View in Activity
              </Button>
            )}
            {(mode === "memory-off" || mode === "empty") && (
              <Button component={Link} to={`/series/${seriesId}/glossary`} variant="light">
                Open Glossary
              </Button>
            )}
            <Button variant="default" onClick={() => setReleasing(true)}>
              {releaseLabel(bootstrap, lang)}
            </Button>
          </Group>
        </Stack>
      </Alert>
    );
  } else if (bootstrap.relockable) {
    const queued = bootstrap.queued_policy_jobs;
    notice = (
      <Alert color="gray" role="status">
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Text size="sm">
            {`Glossary hold is off (${queued.toLocaleString()} ${queued === 1 ? "auto-request" : "auto-requests"} in line).`}
          </Text>
          <Button variant="default" loading={hold.isPending} onClick={() => (queued > 0 ? setHolding(true) : hold.mutate({ released: false }))}>
            Hold again
          </Button>
        </Group>
        {hold.isError && (
          <Text size="sm" c="red">
            {hold.error.message}
          </Text>
        )}
      </Alert>
    );
  }
  // The sheets stay mounted while the page reads the series again: "Released" must outlive the box it came from.
  return (
    <>
      {notice}
      <ReleaseSheet seriesId={seriesId} title={title} bootstrap={bootstrap} opened={releasing} onClose={() => setReleasing(false)} />
      <HoldSheet seriesId={seriesId} title={title} queued={bootstrap.queued_policy_jobs} opened={holding} onClose={() => setHolding(false)} />
    </>
  );
}
