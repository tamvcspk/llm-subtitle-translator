import { Alert, Anchor, Button, Card, Checkbox, Drawer, Group, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router";
import { useQueue, useResearchEstimate, useResearchTerms, useStopJob } from "../api/queries";
import type { ResearchEstimate } from "../api/types";
import { researchJobOf, researchLabel, researchLines, researchOff, stripFor } from "../lib/research";

/**
 * The price of Research terms (UX §6.7, principle 9): what is looked up, the two calls, the models, what is left of the
 * series' budget, and the tick for this run. **Confirm** queues one `research` job; the queue runs it under the day's quota,
 * so the box closes on a line that says where to follow it.
 */
function ResearchSheet({
  opened,
  seriesId,
  lang,
  estimate,
  onClose,
}: {
  opened: boolean;
  seriesId: number;
  lang: string;
  estimate: ResearchEstimate | undefined;
  onClose: () => void;
}) {
  const research = useResearchTerms();
  const [ticked, setTicked] = useState<boolean | null>(null);
  const answer = research.data;
  const autoAccept = ticked ?? estimate?.auto_accept ?? true;

  const close = () => {
    research.reset();
    setTicked(null);
    onClose();
  };
  const queued = answer !== undefined && "queued" in answer ? answer : null;

  return (
    <Drawer opened={opened} onClose={close} position="bottom" size="auto" title="Research terms" padding="lg" radius="lg">
      <Stack>
        {queued ? (
          <>
            <Text>{queued.queued > 0 ? "Queued. Follow it in Activity; what it finds waits in Review." : "A run for this series is already in line."}</Text>
            <Button mih={44} onClick={close}>
              Close
            </Button>
          </>
        ) : estimate === undefined ? (
          <Text c="dimmed">Counting…</Text>
        ) : (
          <>
            {researchLines(estimate).map(([label, value]) => (
              <Group key={label} justify="space-between" wrap="nowrap" gap="md">
                <Text c="dimmed">{label}</Text>
                <Text fw={600} ta="right" style={{ overflowWrap: "anywhere" }}>
                  {value}
                </Text>
              </Group>
            ))}
            <Text size="sm" c="dimmed">
              One search call, then one call that reads its answer. A search may run several queries, so the number is an estimate. Only a
              term the web backs with a page is proposed, and nothing is retranslated.
            </Text>
            <Checkbox
              size="md"
              checked={autoAccept}
              onChange={(event) => setTicked(event.currentTarget.checked)}
              label="Auto-accept terms with 2+ independent sources"
              description="For this run only. Settings, Story and style, holds the standing switch (Auto-accept terms)."
            />
            {research.isError && <Alert color="red">{research.error.message}</Alert>}
            <Group justify="space-between" wrap="wrap">
              <Button variant="default" mih={44} onClick={close}>
                Cancel
              </Button>
              <Button mih={44} disabled={estimate.blocked !== "" || estimate.calls === 0} loading={research.isPending} onClick={() => research.mutate({ seriesId, lang, autoAccept })}>
                Confirm
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}

/**
 * **Research terms (N)** on the Glossary tab of a series (UX §6.7): look the names two episodes share up on the web and send
 * what the web backs to Review. The strip under the button follows the job: running (Stop), waiting, done (Open Review),
 * failed (Retry).
 */
export function ResearchTerms({ seriesId, lang }: { seriesId: number; lang: string }) {
  const estimate = useResearchEstimate(seriesId, lang);
  const queue = useQueue();
  const stop = useStopJob();
  const [asking, setAsking] = useState(false);
  const found = researchJobOf(queue.data, seriesId, lang);
  const strip = found ? stripFor(found.job, found.tab) : null;
  const off = researchOff(estimate.data);
  // A run in line or running is the answer to another click: the worker would only merge into it.
  const busy = found !== null && (found.tab === "pending" || found.tab === "processing" || found.tab === "deferred");

  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label="Research terms">
      <Stack gap="sm">
        <Title order={4}>Research terms</Title>
        <Text size="sm" c="dimmed">
          Looks up on the web how the names and terms two or more episodes share are written in {lang}. What it finds waits in Review; two
          independent sites can apply a term by themselves when Auto-accept terms is on.
        </Text>
        <Group gap="sm" align="center">
          <Button mih={44} disabled={estimate.data === undefined || off !== null || busy} onClick={() => setAsking(true)}>
            {researchLabel(estimate.data?.terms)}
          </Button>
          {off && !busy && (
            <Text size="sm" c="dimmed" role="status">
              {off.reason}
              {off.settings && (
                <>
                  {" · "}
                  <Anchor component={Link} to="/settings" size="sm">
                    Settings
                  </Anchor>
                </>
              )}
            </Text>
          )}
        </Group>
        {estimate.isError && <Text size="sm" c="dimmed">Could not count the terms: {estimate.error.message}</Text>}
        {strip && found && (
          <Group justify="space-between" wrap="wrap" gap="sm" role="status" aria-label="Research status">
            <Text size="sm" c={strip.state === "failed" ? "red" : undefined}>
              {strip.text}
            </Text>
            <Group gap="xs">
              {strip.stop && (
                <Button variant="default" mih={44} loading={stop.isPending} onClick={() => stop.mutate(found.job.job_id)}>
                  Stop
                </Button>
              )}
              {strip.next === "review" && (
                <Button component={Link} to="/review" variant="light" mih={44}>
                  Open Review →
                </Button>
              )}
              {strip.next === "retry" && (
                <Button variant="light" mih={44} disabled={off !== null} onClick={() => setAsking(true)}>
                  Retry
                </Button>
              )}
            </Group>
          </Group>
        )}
        {stop.isError && <Alert color="red">{stop.error.message}</Alert>}
      </Stack>
      <ResearchSheet opened={asking} seriesId={seriesId} lang={lang} estimate={estimate.data} onClose={() => setAsking(false)} />
    </Card>
  );
}
