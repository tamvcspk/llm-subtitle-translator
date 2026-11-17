import { Alert, Button, Drawer, Group, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useMemo, useState } from "react";
import { useAcceptSuggestion, useSuggestionImpact } from "../../api/queries";
import type { Suggestion, SuggestionDecision, SuggestionPayload } from "../../api/types";
import { draftOf, draftProblem, impactLine, payloadOf, sameDraft, type EditDraft } from "../../lib/suggestions";

interface Labels {
  a: string;
  b: string;
  c?: string;
  d?: string;
}

const LABELS: Record<Suggestion["kind"], Labels> = {
  term: { a: "Source", b: "Translation" },
  address_rule: { a: "First character", b: "Second character", c: "First calls second", d: "Second calls first" },
  character: { a: "Name", b: "Translated name", c: "Gender", d: "Description" },
};

function Form({ suggestion, onDone, onClose }: { suggestion: Suggestion; onDone: (answer: SuggestionDecision) => void; onClose: () => void }) {
  const original = draftOf(suggestion);
  const [draft, setDraft] = useState<EditDraft>(original);
  const accept = useAcceptSuggestion();
  const labels = LABELS[suggestion.kind];
  const problem = draftProblem(suggestion.kind, draft);
  const payload = payloadOf(suggestion, draft);
  // The number is the worker's own, for the edited text: a dry run on every pause in typing, never a button.
  const json = JSON.stringify(payload);
  const [settledJson] = useDebouncedValue(json, 400);
  const settled = useMemo(() => JSON.parse(settledJson) as SuggestionPayload, [settledJson]);
  const waiting = settledJson !== json;
  const impact = useSuggestionImpact(suggestion.id, settled, problem === null && !waiting);
  const line = problem ?? (impact.isError || waiting ? null : impactLine(impact.data?.would_stale));
  const set = (patch: Partial<EditDraft>) => setDraft((previous) => ({ ...previous, ...patch }));
  const box = (key: "a" | "b" | "c" | "d", label: string) => (
    <TextInput key={key} label={label} value={draft[key]} onChange={(event) => set({ [key]: event.currentTarget.value })} />
  );
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        The change is written to the glossary of {suggestion.series} ({suggestion.lang}) as you type it here, not as it was proposed.
      </Text>
      {box("a", labels.a)}
      {box("b", labels.b)}
      {labels.c && box("c", labels.c)}
      {labels.d && box("d", labels.d)}
      {suggestion.kind === "term" && (
        <Switch label="Locked: always use this translation" checked={draft.locked} onChange={(event) => set({ locked: event.currentTarget.checked })} />
      )}
      {suggestion.kind === "character" && <TextInput label="Voice (how they speak)" value={draft.speech} onChange={(event) => set({ speech: event.currentTarget.value })} />}
      {suggestion.kind !== "character" && <TextInput label="Note" value={draft.note} onChange={(event) => set({ note: event.currentTarget.value })} />}
      {line && (
        <Text size="sm" c={problem ? "red" : "dimmed"} role="status">
          {line}
        </Text>
      )}
      {impact.isError && <Alert color="red">{impact.error.message}</Alert>}
      {accept.isError && <Alert color="red">{accept.error.message}</Alert>}
      <Group justify="space-between">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={problem !== null || impact.isError}
          loading={accept.isPending}
          onClick={() =>
            accept.mutate(
              // Untouched boxes send no payload: what was proposed is what is accepted.
              { id: suggestion.id, payload: sameDraft(draft, original) ? undefined : payload },
              { onSuccess: onDone },
            )
          }
        >
          Accept edited
        </Button>
      </Group>
    </Stack>
  );
}

/** "Edit, then accept": the proposal in boxes, with what accepting it would flag stale counted live from the worker. */
export function EditSheet({ suggestion, onDone, onClose }: { suggestion: Suggestion | null; onDone: (answer: SuggestionDecision) => void; onClose: () => void }) {
  return (
    <Drawer opened={suggestion !== null} onClose={onClose} position="bottom" size="auto" title="Edit, then accept" padding="lg" radius="lg">
      {suggestion && <Form key={suggestion.id} suggestion={suggestion} onDone={onDone} onClose={onClose} />}
    </Drawer>
  );
}
