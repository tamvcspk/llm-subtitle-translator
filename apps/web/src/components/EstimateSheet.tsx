import { Alert, Button, Drawer, Group, Loader, Stack, Text } from "@mantine/core";
import { useRequestJobs, useEstimate, type CostRequest } from "../api/queries";
import type { Estimate, JobsAnswer } from "../api/types";
import { clock } from "../lib/format";

/** `action` is "Translate missing · Arcane": what the person is about to pay for. */
export type EstimateRequest = { action: string } & CostRequest;

function Line({ label, value }: { label: string; value: string }) {
  return (
    <Group justify="space-between" wrap="nowrap">
      <Text c="dimmed">{label}</Text>
      <Text fw={600}>{value}</Text>
    </Group>
  );
}

/** Rows of the box in wireframe 0d; a number the planner cannot know is left out, not shown as zero. */
export function estimateLines(estimate: Estimate): Array<[string, string]> {
  const lines: Array<[string, string]> = [
    ["Jobs", estimate.jobs.toLocaleString()],
    ["Cues", estimate.cues.toLocaleString()],
    ["Model calls (est.)", estimate.calls === null ? "unknown" : estimate.calls.toLocaleString()],
  ];
  if (estimate.calls_left_today !== null) lines.push(["Calls left today", estimate.calls_left_today.toLocaleString()]);
  if (estimate.paused_until !== null) lines.push(["Paused until", clock(estimate.paused_until)]);
  if (estimate.minutes !== null) {
    const done = estimate.finish_at === null ? "" : ` · done ~${clock(estimate.finish_at)}`;
    lines.push(["Time", `~${Math.max(1, Math.round(estimate.minutes))} min${done}`]);
  }
  return lines;
}

/** The request without its title, so the same box asked twice for the same thing shares one query key. */
function withoutAction(request: EstimateRequest): CostRequest {
  return "retry" in request ? { retry: request.retry } : { selector: request.selector };
}

function skippedNote(answer: JobsAnswer): string | null {
  if (answer.skipped_total === 0) return null;
  const reasons = Object.entries(answer.skipped_by_reason).map(([reason, n]) => `${n} ${reason}`);
  return `Left out: ${reasons.join(", ")}.`;
}

/** The bottom sheet every button that spends model calls goes through (UX §6.4). */
export function EstimateSheet({ request, onClose }: { request: EstimateRequest | null; onClose: () => void }) {
  const estimate = useEstimate(request ? withoutAction(request) : null);
  const make = useRequestJobs();
  const answer = estimate.data;
  const done = make.data;

  const close = () => {
    make.reset();
    onClose();
  };

  return (
    <Drawer
      opened={request !== null}
      onClose={close}
      position="bottom"
      size="auto"
      title={request?.action ?? ""}
      padding="lg"
      radius="lg"
    >
      <Stack>
        {done ? (
          <>
            <Text>
              {done.count === 1 ? "1 job is in line." : `${done.count} jobs are in line.`} They start when the worker is free.
            </Text>
            <Button onClick={close}>Close</Button>
          </>
        ) : (
          <>
            {estimate.isPending && request !== null && <Loader size="sm" aria-label="Estimating" />}
            {estimate.isError && <Alert color="red">{estimate.error.message}</Alert>}
            {answer && (
              <Stack gap="xs">
                {estimateLines(answer.estimate).map(([label, value]) => (
                  <Line key={label} label={label} value={value} />
                ))}
                {answer.estimate.jobs === 0 && <Text c="dimmed">Nothing to translate.</Text>}
                {skippedNote(answer) && (
                  <Text size="sm" c="dimmed">
                    {skippedNote(answer)}
                  </Text>
                )}
              </Stack>
            )}
            {make.isError && <Alert color="red">{make.error.message}</Alert>}
            <Group justify="space-between">
              <Button variant="default" onClick={close}>
                Cancel
              </Button>
              <Button
                disabled={!answer || answer.estimate.jobs === 0}
                loading={make.isPending}
                onClick={() => request && make.mutate(withoutAction(request))}
              >
                Confirm
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}
