import { Alert, Button, Drawer, Group, Loader, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useRequestJobs, useEstimate, type CostRequest } from "../api/queries";
import type { Estimate, EstimateAnswer } from "../api/types";
import { clock } from "../lib/format";
import { languageName } from "../lib/home";

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

/** "3 calls · gemini-3.8-flash": a count of calls and the model they run on (the model is left out when the answer names none). */
export function callsText(count: number, model?: string): string {
  return `${count.toLocaleString()} ${count === 1 ? "call" : "calls"}${model ? ` · ${model}` : ""}`;
}

/**
 * Rows of the box in wireframe 0d; a number the planner cannot know is left out, not shown as zero. Each group of calls
 * names the model it runs on (P19a): `callsLabel` is the main group ("Translate (est.)", or "Judge (est.)" for the judge),
 * `jobsLabel` what the jobs are called ("Episodes to release" for the Bootstrap box).
 */
export function estimateLines(
  estimate: Estimate,
  options: { callsLabel?: string; jobsLabel?: string } = {},
): Array<[string, string]> {
  const model = estimate.model ?? "";
  const lines: Array<[string, string]> = [
    [options.jobsLabel ?? "Jobs", estimate.jobs.toLocaleString()],
    ["Cues", estimate.cues.toLocaleString()],
    [options.callsLabel ?? "Translate (est.)", estimate.calls === null ? `unknown${model ? ` · ${model}` : ""}` : callsText(estimate.calls, model)],
  ];
  // Reading the story so far is not translation: its own row, with the model it runs on (FR-15.5.1).
  if (estimate.extract_calls > 0) lines.push(["Extract facts", callsText(estimate.extract_calls, estimate.extract_model)]);
  // Condensing the old story summaries is memory work too (FR-5.8): its own row, on its own model.
  const condensing = estimate.summarize_calls ?? 0;
  if (condensing > 0) lines.push(["Condense story", callsText(condensing, estimate.summarize_model)]);
  // Two or more groups of calls: the sum, so a person sees what the quota pays (it counts what is known).
  const groups = [estimate.calls ?? 0, estimate.extract_calls, condensing].filter((n) => n > 0).length;
  if (groups >= 2) lines.push(["Total calls (est.)", ((estimate.calls ?? 0) + estimate.extract_calls + condensing).toLocaleString()]);
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
  if ("retry" in request) return { retry: request.retry };
  if ("prerequisites" in request) return { prerequisites: request.prerequisites };
  if ("retranslate" in request) return { retranslate: request.retranslate };
  if ("variant" in request) return { variant: request.variant };
  if ("judge" in request) return { judge: request.judge };
  return { selector: request.selector };
}

function skippedNote(answer: EstimateAnswer): string | null {
  if (answer.skipped_total === 0) return null;
  const reasons = Object.entries(answer.skipped_by_reason).map(([reason, n]) => `${n} ${reason}`);
  return `Left out: ${reasons.join(", ")}.`;
}

/** Two-group breakdown for multi-item Translate / Retranslate requests (P18c). */
export function planBreakdown(answer: EstimateAnswer, langCode?: string): string[] {
  const newCount = answer.translate_total ?? 0;
  const retranslateCount = answer.retranslate_total ?? 0;
  if (retranslateCount === 0 && answer.mode !== "auto") return [];
  const code = langCode || answer.planned?.[0]?.lang || "";
  const langLabel = code ? languageName(code) : "target";
  const lines: string[] = [];
  if (newCount > 0) {
    lines.push(
      `New translations: ${newCount.toLocaleString()} ${newCount === 1 ? "episode" : "episodes"} (no ${langLabel} subtitle yet)`,
    );
  }
  if (retranslateCount > 0) {
    lines.push(
      `Retranslations: ${retranslateCount.toLocaleString()} ${retranslateCount === 1 ? "episode" : "episodes"} (already translated; previous file kept as a version)`,
    );
  }
  return lines;
}

/** The bottom sheet every button that spends model calls goes through (UX §6.4). */
export function EstimateSheet({ request, onClose }: { request: EstimateRequest | null; onClose: () => void }) {
  const estimate = useEstimate(request ? withoutAction(request) : null);
  const make = useRequestJobs();
  const [confirmFor, setConfirmFor] = useState<EstimateRequest | null>(null);
  const confirmOverwrite = request !== null && confirmFor === request;
  const answer = estimate.data;
  const done = make.data;
  const retranslateCount = answer?.retranslate_total ?? 0;
  const langCode = request && "selector" in request ? request.selector.languages?.[0] : undefined;
  const breakdown = answer ? planBreakdown(answer, langCode) : [];

  const close = () => {
    setConfirmFor(null);
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
                {estimateLines(answer.estimate, { callsLabel: request && "judge" in request ? "Judge (est.)" : undefined }).map(([label, value]) => (
                  <Line key={label} label={label} value={value} />
                ))}
                {breakdown.map((line) => (
                  <Text key={line} size="sm">
                    {line}
                  </Text>
                ))}
                {answer.estimate.jobs === 0 && <Text c="dimmed">Nothing to translate.</Text>}
                {skippedNote(answer) && (
                  <Text size="sm" c="dimmed">
                    {skippedNote(answer)}
                  </Text>
                )}
              </Stack>
            )}
            {confirmOverwrite && retranslateCount > 0 && (
              <Alert color="orange" title={`Overwrite ${retranslateCount.toLocaleString()} ${retranslateCount === 1 ? "translation" : "translations"}?`}>
                Each existing subtitle is kept as a version before it is replaced, so you can revert it any time.
              </Alert>
            )}
            {make.isError && <Alert color="red">{make.error.message}</Alert>}
            <Group justify="space-between">
              {confirmOverwrite ? (
                <Button variant="default" onClick={() => setConfirmFor(null)}>
                  Back
                </Button>
              ) : (
                <Button variant="default" onClick={close}>
                  Cancel
                </Button>
              )}
              {retranslateCount > 0 && !confirmOverwrite ? (
                <Button disabled={!answer || answer.estimate.jobs === 0} onClick={() => setConfirmFor(request)}>
                  Confirm
                </Button>
              ) : (
                <Button
                  color={confirmOverwrite ? "orange" : undefined}
                  disabled={!answer || answer.estimate.jobs === 0}
                  loading={make.isPending}
                  onClick={() => request && make.mutate(withoutAction(request))}
                >
                  {confirmOverwrite
                    ? `Overwrite ${retranslateCount.toLocaleString()} ${retranslateCount === 1 ? "translation" : "translations"}`
                    : "Confirm"}
                </Button>
              )}
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}
