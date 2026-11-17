import { Alert, Anchor, Button, Card, Group, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  useAcceptSuggestion,
  useRejectSuggestion,
  useSuggestions,
  useSuggestionSummary,
  useUndoSuggestion,
} from "../api/queries";
import type { BulkAnswer, Suggestion, SuggestionDecision } from "../api/types";
import { BulkSheet } from "../components/review/BulkSheet";
import { EditSheet } from "../components/review/EditSheet";
import { DecidedCard, PendingCard } from "../components/review/SuggestionCard";
import { describe, groupBySeries, pendingLine, type SeriesGroup } from "../lib/suggestions";

/** What the last decision did, with Undo while it can still be undone. */
interface Notice {
  text: string;
  undo?: Suggestion;
}

function staleSentence(stale: number, again = false): string {
  if (stale === 0) return "No translation is affected.";
  return `${stale.toLocaleString()} translation${stale === 1 ? " is" : "s are"} ${again ? "Stale again" : "now Stale"}; none is redone.`;
}

function SeriesBlock({
  group,
  count,
  working,
  onAccept,
  onEdit,
  onReject,
  onAll,
}: {
  group: SeriesGroup;
  count: number;
  working: number | null;
  onAccept: (suggestion: Suggestion) => void;
  onEdit: (suggestion: Suggestion) => void;
  onReject: (suggestion: Suggestion) => void;
  onAll: (group: SeriesGroup, count: number) => void;
}) {
  return (
    <Stack gap="sm" component="section" aria-label={group.series}>
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Stack gap={0}>
          <Anchor component={Link} to={`/series/${group.seriesId}/glossary`} fw={600} size="lg">
            {group.series}
          </Anchor>
          <Text size="sm" c="dimmed">
            {pendingLine(count)} waiting
          </Text>
        </Stack>
        <Button variant="light" onClick={() => onAll(group, count)} aria-label={`Accept all in ${group.series}`}>
          Accept all ({count})
        </Button>
      </Group>
      <Stack gap="sm" component="ul" aria-label={`${group.series} suggestions`} style={{ margin: 0, padding: 0 }}>
        {group.items.map((item) => (
          <PendingCard key={item.id} suggestion={item} busy={working === item.id} onAccept={onAccept} onEdit={onEdit} onReject={onReject} />
        ))}
      </Stack>
    </Stack>
  );
}

/**
 * Review (UX A.6): what the machine proposes for the glossary and the character cards, grouped by series. Accepting
 * writes the glossary and flags translations Stale (the number is the worker's own, counted before you decide); it
 * never retranslates. Rejecting is remembered, so the same thing is not proposed again.
 */
export function Review() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "history" ? "history" : "pending";
  const summary = useSuggestionSummary();
  const pending = useSuggestions("pending", tab === "pending");
  const history = useSuggestions("decided", tab === "history");
  const accept = useAcceptSuggestion();
  const reject = useRejectSuggestion();
  const undo = useUndoSuggestion();

  const [notice, setNotice] = useState<Notice | null>(null);
  const [editing, setEditing] = useState<Suggestion | null>(null);
  const [bulk, setBulk] = useState<{ seriesId: number; series: string; count: number } | null>(null);
  const [working, setWorking] = useState<number | null>(null);

  const error = accept.error ?? reject.error ?? undo.error;
  const accepted = (suggestion: Suggestion, answer: SuggestionDecision) =>
    setNotice({ text: `Accepted “${describe(suggestion).title}”. ${staleSentence(answer.would_stale)}`, undo: answer.suggestion?.undoable ? answer.suggestion : undefined });

  const doAccept = (suggestion: Suggestion) => {
    setWorking(suggestion.id);
    accept.mutate({ id: suggestion.id }, { onSuccess: (answer) => accepted(suggestion, answer), onSettled: () => setWorking(null) });
  };
  const doReject = (suggestion: Suggestion) => {
    setWorking(suggestion.id);
    reject.mutate(suggestion.id, {
      onSuccess: () => setNotice({ text: `Rejected “${describe(suggestion).title}”. It will not be proposed again.` }),
      onSettled: () => setWorking(null),
    });
  };
  const doUndo = (suggestion: Suggestion) => {
    setWorking(suggestion.id);
    undo.mutate(suggestion.id, {
      onSuccess: (answer) => setNotice({ text: `Undone: “${describe(suggestion).title}” is back in the inbox. ${staleSentence(answer.would_stale, true)}` }),
      onSettled: () => setWorking(null),
    });
  };
  const bulkDone = (answer: BulkAnswer, series: string) => {
    setBulk(null);
    const failed = answer.failed?.length ?? 0;
    setNotice({
      text: `Accepted ${pendingLine(answer.count)} for ${series}${failed ? `; ${failed} could not be applied and stay in the inbox` : ""}. ${staleSentence(answer.would_stale)}`,
    });
  };

  const choose = (value: string | null) => {
    if (value) setParams(value === "history" ? { tab: "history" } : {}, { replace: true });
  };

  const counts = summary.data?.counts;
  const perSeries = new Map((summary.data?.series ?? []).map((row) => [row.series_id, row.pending]));
  const list = tab === "pending" ? pending : history;
  const groups = groupBySeries(list.data?.items ?? []);

  return (
    <Stack gap="md">
      <Title order={2}>Review</Title>
      <Text c="dimmed">
        The machine proposes terms, forms of address and characters here. Accepting changes the glossary only: translations made without it are flagged
        Stale, and nothing is retranslated until you ask.
      </Text>

      {notice && (
        <Alert color="teal" withCloseButton onClose={() => setNotice(null)} role="status">
          <Group justify="space-between" wrap="wrap" gap="xs">
            <Text size="sm">{notice.text}</Text>
            {notice.undo && (
              <Button variant="default" loading={undo.isPending} onClick={() => notice.undo && doUndo(notice.undo)}>
                Undo
              </Button>
            )}
          </Group>
        </Alert>
      )}
      {error && <Alert color="red">{error.message}</Alert>}

      <Tabs value={tab} onChange={choose} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="pending" style={{ minHeight: 44 }}>
            Pending {counts?.pending ?? ""}
          </Tabs.Tab>
          <Tabs.Tab value="history" style={{ minHeight: 44 }}>
            History {counts ? counts.accepted + counts.rejected : ""}
          </Tabs.Tab>
        </Tabs.List>
      </Tabs>

      {list.isPending && <Loader size="sm" aria-label="Loading suggestions" />}
      {list.isError && <Alert color="red">{list.error.message}</Alert>}

      {list.data && groups.length === 0 && (
        <Text c="dimmed">{tab === "pending" ? "Nothing is waiting for review." : "Nothing has been decided yet."}</Text>
      )}

      {tab === "pending" &&
        groups.map((group) => (
          <SeriesBlock
            key={group.seriesId}
            group={group}
            count={perSeries.get(group.seriesId) ?? group.items.length}
            working={working}
            onAccept={doAccept}
            onEdit={setEditing}
            onReject={doReject}
            onAll={(chosen, count) => setBulk({ seriesId: chosen.seriesId, series: chosen.series, count })}
          />
        ))}

      {tab === "history" &&
        groups.map((group) => (
          <Card key={group.seriesId} withBorder={false} padding={0} component="section" aria-label={group.series}>
            <Stack gap="sm">
              <Text fw={600} size="lg">
                {group.series}
              </Text>
              <Stack gap="sm" component="ul" aria-label={`${group.series} decided`} style={{ margin: 0, padding: 0 }}>
                {group.items.map((item) => (
                  <DecidedCard key={item.id} suggestion={item} busy={working === item.id} onUndo={doUndo} />
                ))}
              </Stack>
            </Stack>
          </Card>
        ))}

      <EditSheet
        suggestion={editing}
        onDone={(answer) => {
          if (editing) accepted(editing, answer);
          setEditing(null);
        }}
        onClose={() => setEditing(null)}
      />
      <BulkSheet group={bulk} onDone={bulkDone} onClose={() => setBulk(null)} />
    </Stack>
  );
}
