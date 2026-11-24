import { Alert, Anchor, Button, Card, Group, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  useAcceptSuggestion,
  useCatalog,
  useRejectSuggestion,
  useSuggestions,
  useSuggestionSummary,
  useUndoSuggestion,
} from "../api/queries";
import type { BulkAnswer, Suggestion, SuggestionDecision } from "../api/types";
import { BulkSheet } from "../components/review/BulkSheet";
import { EditSheet } from "../components/review/EditSheet";
import { DecidedCard, PendingCard } from "../components/review/SuggestionCard";
import { seriesRoute } from "../lib/seriesLink";
import { describe, groupBySeries, pendingLine, type SeriesGroup } from "../lib/suggestions";

/** What a completed decision did, with Undo while it can still be undone. */
interface Notice {
  key: string;
  text: string;
  undo?: Suggestion;
}

function staleSentence(stale: number, again = false): string {
  if (stale === 0) return "No translation is affected.";
  return `${stale.toLocaleString()} translation${stale === 1 ? " is" : "s are"} ${again ? "Stale again" : "now Stale"}; none is redone.`;
}

function SeriesBlock({
  group,
  href,
  count,
  working,
  onAccept,
  onEdit,
  onReject,
  onAll,
}: {
  group: SeriesGroup;
  /** The series page, or `null` when the catalog has no row for it (the name is then plain text). */
  href: string | null;
  count: number;
  working: ReadonlySet<number>;
  onAccept: (suggestion: Suggestion) => void;
  onEdit: (suggestion: Suggestion) => void;
  onReject: (suggestion: Suggestion) => void;
  onAll: (group: SeriesGroup, count: number) => void;
}) {
  return (
    <Stack gap="sm" component="section" aria-label={group.series}>
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Stack gap={0}>
          {href ? (
            <Anchor component={Link} to={href} fw={600} size="lg">
              {group.series}
            </Anchor>
          ) : (
            <Text fw={600} size="lg">
              {group.series}
            </Text>
          )}
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
          <PendingCard key={item.id} suggestion={item} busy={working.has(item.id)} onAccept={onAccept} onEdit={onEdit} onReject={onReject} />
        ))}
      </Stack>
    </Stack>
  );
}

/**
 * Reusable Review inbox (UX A.6), rendered both as the top-level Review page and (filtered to one series) as the
 * Suggestions tab on a series page (P18e). Parallel actions track per-card working ids and keep individual notices
 * via `mutateAsync` so clicking a second card never stops or drops the first (P18b).
 */
export function ReviewInbox({ seriesId, embedded = false }: { seriesId?: number; embedded?: boolean }) {
  const [params, setParams] = useSearchParams();
  const tabKey = embedded ? "rtab" : "tab";
  const tab = params.get(tabKey) === "history" ? "history" : "pending";
  const summary = useSuggestionSummary();
  const catalog = useCatalog();
  const pending = useSuggestions("pending", tab === "pending", seriesId);
  const history = useSuggestions("decided", tab === "history" || seriesId !== undefined, seriesId);
  const accept = useAcceptSuggestion();
  const reject = useRejectSuggestion();
  const undo = useUndoSuggestion();

  const [notices, setNotices] = useState<Notice[]>([]);
  const [errors, setErrors] = useState<Array<{ key: string; text: string }>>([]);
  const [editing, setEditing] = useState<Suggestion | null>(null);
  const [bulk, setBulk] = useState<{ seriesId: number; series: string; count: number } | null>(null);
  const [working, setWorking] = useState<ReadonlySet<number>>(new Set());

  const startWork = (id: number) =>
    setWorking((previous) => {
      const next = new Set(previous);
      next.add(id);
      return next;
    });
  const endWork = (id: number) =>
    setWorking((previous) => {
      if (!previous.has(id)) return previous;
      const next = new Set(previous);
      next.delete(id);
      return next;
    });
  const pushNotice = (notice: Omit<Notice, "key">) =>
    setNotices((previous) => [{ ...notice, key: `${Date.now()}-${Math.random()}` }, ...previous].slice(0, 6));
  const pushError = (err: unknown) => {
    const text = err instanceof Error ? err.message : String(err);
    setErrors((previous) => [{ key: `${Date.now()}-${Math.random()}`, text }, ...previous].slice(0, 4));
  };

  const accepted = (suggestion: Suggestion, answer: SuggestionDecision) =>
    pushNotice({
      text: `Accepted “${describe(suggestion).title}”. ${staleSentence(answer.would_stale)}`,
      undo: answer.suggestion?.undoable ? answer.suggestion : undefined,
    });

  const doAccept = async (suggestion: Suggestion) => {
    if (working.has(suggestion.id)) return;
    startWork(suggestion.id);
    try {
      const answer = await accept.mutateAsync({ id: suggestion.id });
      accepted(suggestion, answer);
    } catch (err) {
      pushError(err);
    } finally {
      endWork(suggestion.id);
    }
  };
  const doReject = async (suggestion: Suggestion) => {
    if (working.has(suggestion.id)) return;
    startWork(suggestion.id);
    try {
      await reject.mutateAsync(suggestion.id);
      pushNotice({ text: `Rejected “${describe(suggestion).title}”. It will not be proposed again.` });
    } catch (err) {
      pushError(err);
    } finally {
      endWork(suggestion.id);
    }
  };
  const doUndo = async (suggestion: Suggestion, noticeKey?: string) => {
    if (working.has(suggestion.id)) return;
    startWork(suggestion.id);
    try {
      const answer = await undo.mutateAsync(suggestion.id);
      if (noticeKey) setNotices((previous) => previous.filter((n) => n.key !== noticeKey));
      pushNotice({ text: `Undone: “${describe(suggestion).title}” is back in the inbox. ${staleSentence(answer.would_stale, true)}` });
    } catch (err) {
      pushError(err);
    } finally {
      endWork(suggestion.id);
    }
  };
  const bulkDone = (answer: BulkAnswer, series: string) => {
    setBulk(null);
    const failed = answer.failed?.length ?? 0;
    pushNotice({
      text: `Accepted ${pendingLine(answer.count)} for ${series}${failed ? `; ${failed} could not be applied and stay in the inbox` : ""}. ${staleSentence(answer.would_stale)}`,
    });
  };

  const choose = (value: string | null) => {
    if (!value) return;
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value === "history") next.set(tabKey, "history");
        else next.delete(tabKey);
        return next;
      },
      { replace: true },
    );
  };

  const counts = summary.data?.counts;
  const perSeries = new Map((summary.data?.series ?? []).map((row) => [row.series_id, row.pending]));
  const list = tab === "pending" ? pending : history;
  const groups = groupBySeries(list.data?.items ?? []);
  const pendingTabCount = seriesId !== undefined ? (perSeries.get(seriesId) ?? 0) : counts?.pending;
  const historyTabCount = seriesId !== undefined ? history.data?.items.length : counts ? counts.accepted + counts.rejected : undefined;

  return (
    <Stack gap="md">
      {!embedded && <Title order={2}>Review</Title>}
      <Text c="dimmed">
        The machine proposes terms, forms of address and characters here. Accepting changes the glossary only: translations made without it are flagged
        Stale, and nothing is retranslated until you ask.
      </Text>

      {notices.map((notice) => (
        <Alert key={notice.key} color="teal" withCloseButton onClose={() => setNotices((prev) => prev.filter((n) => n.key !== notice.key))} role="status">
          <Group justify="space-between" wrap="wrap" gap="xs">
            <Text size="sm">{notice.text}</Text>
            {notice.undo && (
              <Button variant="default" loading={working.has(notice.undo.id)} onClick={() => notice.undo && void doUndo(notice.undo, notice.key)}>
                Undo
              </Button>
            )}
          </Group>
        </Alert>
      ))}
      {errors.map((err) => (
        <Alert key={err.key} color="red" withCloseButton onClose={() => setErrors((prev) => prev.filter((e) => e.key !== err.key))}>
          {err.text}
        </Alert>
      ))}

      <Tabs value={tab} onChange={choose} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="pending" style={{ minHeight: 44 }}>
            Pending {pendingTabCount ?? ""}
          </Tabs.Tab>
          <Tabs.Tab value="history" style={{ minHeight: 44 }}>
            History {historyTabCount ?? ""}
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
            href={embedded ? null : seriesRoute(catalog.data, group.seriesId, "glossary")}
            count={perSeries.get(group.seriesId) ?? group.items.length}
            working={working}
            onAccept={(s) => void doAccept(s)}
            onEdit={setEditing}
            onReject={(s) => void doReject(s)}
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
                  <DecidedCard key={item.id} suggestion={item} busy={working.has(item.id)} onUndo={(s) => void doUndo(s)} />
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

export function Review() {
  return <ReviewInbox />;
}
