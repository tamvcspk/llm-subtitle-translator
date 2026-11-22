import { Alert, Anchor, Badge, Button, Card, Group, Loader, SegmentedControl, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useCatalog, useFromEdits, useInsights, useLibrarySeries, useSeriesUnits } from "../api/queries";
import { routeUrl } from "../api/route";
import type { InsightsBreakdown, InsightsDay, LibrarySeriesRow } from "../api/types";
import {
  abLabel,
  against,
  barWidth,
  count,
  DAY_CHOICES,
  dailyLimit,
  duration,
  fromEditsLine,
  millis,
  outcomeLabel,
  percent,
  reviewLabel,
} from "../lib/insights";
import { seriesRoute } from "../lib/seriesLink";

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label={title}>
      <Stack gap="sm">
        <Stack gap={2}>
          <Title order={3} size="h4">
            {title}
          </Title>
          {note && (
            <Text size="sm" c="dimmed">
              {note}
            </Text>
          )}
        </Stack>
        {children}
      </Stack>
    </Card>
  );
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <Stack gap={2} role="group" aria-label={label}>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text fw={700} size="xl">
        {value}
      </Text>
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Stack>
  );
}

const GRID = { base: 1, xs: 2, md: 3 } as const;

/** One model, or one prompt version, as a card: it folds like a table row does below 1024 px. */
function BreakdownCard({ row, name }: { row: InsightsBreakdown; name: string }) {
  return (
    <Card withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
      <Stack gap="xs">
        <Text fw={600}>{name || "(not recorded)"}</Text>
        <SimpleGrid cols={{ base: 2, md: 4 }} spacing="xs">
          <Stat label="Episodes" value={count(row.episodes)} hint={`${count(row.cues)} cues`} />
          <Stat label="Cues you corrected" value={percent(row.cue_edit_rate)} hint={`${count(row.cue_edits)} cues`} />
          <Stat label="Repairs" value={percent(row.repair_rate)} hint="of translate calls" />
          <Stat label="Calls" value={count(row.calls)} hint={`${count(row.tokens_in + row.tokens_out)} tokens`} />
        </SimpleGrid>
        {row.retranslations !== null && <Text size="sm">{count(row.retranslations)} retranslated after you read them.</Text>}
      </Stack>
    </Card>
  );
}

function Breakdown({ title, note, rows, by }: { title: string; note: string; rows: InsightsBreakdown[]; by: "model" | "prompt_version" }) {
  return (
    <Section title={title} note={note}>
      {rows.length === 0 ? (
        <Text c="dimmed">Nothing has been translated in this window.</Text>
      ) : (
        <Stack gap="sm" component="ul" aria-label={title} style={{ margin: 0, padding: 0 }}>
          {rows.map((row) => (
            <BreakdownCard key={row[by] ?? ""} row={row} name={row[by] ?? ""} />
          ))}
        </Stack>
      )}
    </Section>
  );
}

function Days({ days }: { days: InsightsDay[] }) {
  return (
    <Stack gap={4} component="ul" aria-label="Days" style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {[...days].reverse().map((day) => (
        <Group key={day.day} component="li" wrap="nowrap" gap="sm" align="center" style={{ minHeight: 28 }}>
          <Text size="sm" w={86} style={{ flex: "none" }}>
            {day.day}
          </Text>
          <div aria-hidden style={{ flex: 1, minWidth: 0, background: "var(--mantine-color-default-border)", borderRadius: 4, height: 8 }}>
            <div style={{ width: `${barWidth(day, days)}%`, height: 8, borderRadius: 4, background: "var(--mantine-primary-color-filled)" }} />
          </div>
          <Text size="sm" style={{ flex: "none", textAlign: "right" }} miw={150}>
            {count(day.calls)} calls
            {day.failed > 0 ? `, ${count(day.failed)} failed` : ""}
            {day.cue_edits > 0 ? `, ${count(day.cue_edits)} corrected` : ""}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

/** The golden set and the look for repeated corrections of one series, one row per language it has. */
function SeriesCorrections({ series, href }: { series: LibrarySeriesRow; href: string | null }) {
  const languages = useSeriesUnits(series.id).data?.languages ?? [];
  const look = useFromEdits();
  const [said, setSaid] = useState<{ lang: string; line: string } | null>(null);
  if (languages.length === 0) return null;
  return (
    <Stack gap="xs" component="li" style={{ listStyle: "none" }} aria-label={series.title}>
      {href ? (
        <Anchor component={Link} to={href} fw={600}>
          {series.title}
        </Anchor>
      ) : (
        <Text fw={600}>{series.title}</Text>
      )}
      {languages.map((lang) => (
        <Group key={lang} gap="sm" wrap="wrap">
          <Badge variant="light" size="lg" radius="sm">
            {lang}
          </Badge>
          <Button
            variant="default"
            loading={look.isPending && look.variables?.lang === lang && look.variables.seriesId === series.id}
            onClick={() =>
              look.mutate({ seriesId: series.id, lang }, { onSuccess: (answer) => setSaid({ lang, line: fromEditsLine(answer) }) })
            }
            aria-label={`Look at my ${lang} edits of ${series.title}`}
          >
            Look at my edits
          </Button>
          <Button
            component="a"
            variant="default"
            href={routeUrl("/api/series/{series_id}/golden", { path: { series_id: series.id }, query: { lang, download: "1" } })}
            aria-label={`Download the ${lang} golden set of ${series.title}`}
          >
            Download golden set
          </Button>
        </Group>
      ))}
      {said && <Text size="sm" role="status">{`${said.lang}: ${said.line}`}</Text>}
      {look.isError && <Alert color="red">{look.error.message}</Alert>}
    </Stack>
  );
}

/**
 * Insights (FR-19.5, UX A.7): how the worker is doing and how good its translations are, worked out from its own records.
 * The figure that matters most is the share of cues you had to correct, by model and prompt version: measure before you
 * change a prompt or a style, then again after.
 */
export function Insights() {
  const [days, setDays] = useState<number>(30);
  const insights = useInsights(days);
  const series = useLibrarySeries();
  const catalog = useCatalog();
  const data = insights.data;

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
        <Stack gap={2}>
          <Title order={2}>Insights</Title>
          <Text c="dimmed">Worked out from what the worker recorded. Nothing here costs a model call.</Text>
        </Stack>
        <SegmentedControl
          aria-label="Window"
          value={String(days)}
          onChange={(value) => setDays(Number(value))}
          data={DAY_CHOICES.map((choice) => ({ value: String(choice), label: `${choice} days` }))}
          styles={{ label: { minHeight: 44, display: "flex", alignItems: "center" } }}
        />
      </Group>

      {insights.isPending && <Loader size="sm" aria-label="Loading the figures" />}
      {insights.isError && <Alert color="red">{insights.error.message}</Alert>}

      {data && (
        <>
          <Section title="Quality" note="What you had to fix, and what the review found, over the window.">
            <SimpleGrid cols={GRID}>
              <Stat
                label="Cues you corrected"
                value={percent(data.quality.cue_edits.rate)}
                hint={`${count(data.quality.cue_edits.cues)} of ${count(data.quality.cues_translated)} cues translated`}
              />
              <Stat
                label="Repairs"
                value={percent(data.quality.repair.rate)}
                hint={`${count(data.quality.repair.calls)} repair or split calls of ${count(data.quality.repair.translate_calls)}`}
              />
              <Stat
                label="Retranslated"
                value={percent(data.quality.retranslations.rate)}
                hint={`${count(data.quality.retranslations.count)} of ${count(data.quality.episodes)} episodes; ${count(data.quality.retranslations.reverts)} reverted`}
              />
              <Stat label="Read too fast" value={percent(data.quality.review.cps_rate)} hint={`${count(data.quality.review.cps)} cues over 20 characters a second`} />
              <Stat label="Lines too long" value={percent(data.quality.review.too_long_rate)} hint={`${count(data.quality.review.too_long)} cues over 42 characters a line`} />
            </SimpleGrid>
            {Object.keys(data.quality.review.by_kind).length > 0 && (
              <Text size="sm" c="dimmed">
                Review found: {Object.entries(data.quality.review.by_kind).map(([kind, n]) => `${reviewLabel(kind)} ${count(n)}`).join(" · ")}
              </Text>
            )}
          </Section>

          <Breakdown
            title="By model"
            note="The same figures for each model. A model you corrected more often is the weaker one for your shows."
            rows={data.by_model}
            by="model"
          />
          <Breakdown
            title="By prompt version"
            note="Measure before you change a prompt or a style, then again after: the version that wrote a cue is the one that is blamed for your correction."
            rows={data.by_prompt_version}
            by="prompt_version"
          />

          <Section title="Style trials" note="Your verdicts on variants, added up by style and prompt version. A win counts one, a tie a half.">
            {data.ab.length === 0 ? (
              <Text c="dimmed">No variant has been judged cue by cue yet.</Text>
            ) : (
              <Stack gap="xs" component="ul" aria-label="Style trials" style={{ margin: 0, padding: 0, listStyle: "none" }}>
                {data.ab.map((row) => (
                  <Group key={`${row.style}|${row.prompt_version}`} component="li" justify="space-between" wrap="wrap" gap="xs">
                    <Text fw={600}>{abLabel(row)}</Text>
                    <Text size="sm">
                      Score {percent(row.score)} · {count(row.variant)} for the variant, {count(row.current)} for the current, {count(row.tie)} ties
                    </Text>
                  </Group>
                ))}
              </Stack>
            )}
          </Section>

          <Section title="Now" note="Live: the queue, the models and today's calls.">
            <SimpleGrid cols={GRID}>
              <Stat
                label="Queue"
                value={count(data.operations.queue.depth)}
                hint={`${count(data.operations.queue.counts.processing)} running, ${count(data.operations.queue.counts.pending)} requested, ${count(data.operations.queue.counts.deferred)} waiting`}
              />
              <Stat
                label="Calls today"
                value={against(data.operations.usage.calls_today, dailyLimit(data.operations.usage.limits), "calls")}
                hint={data.operations.daily_quota_pause ? "Paused: the daily quota is spent." : undefined}
              />
              <Stat
                label="Calls in the last minute"
                value={against(data.operations.usage.calls_last_minute, data.operations.usage.limits.rpm, "calls")}
              />
            </SimpleGrid>
            <Group gap="xs" aria-label="Models">
              {data.operations.providers.length === 0 && <Text size="sm" c="dimmed">No model is configured.</Text>}
              {data.operations.providers.map((provider) => (
                <Badge key={provider.model} color={provider.state === "closed" ? "teal" : "orange"} variant="light" size="lg" radius="sm">
                  {provider.model}: {provider.state === "closed" ? "working" : provider.state.replace("_", " ")}
                </Badge>
              ))}
            </Group>
          </Section>

          <Section title="Speed and cost" note="Per episode, from the translation calls of the window.">
            <SimpleGrid cols={GRID}>
              <Stat label="Episodes translated" value={count(data.operations.per_episode.episodes)} />
              <Stat
                label="Tokens per episode"
                value={`${count(data.operations.per_episode.tokens_in)} in`}
                hint={`${count(data.operations.per_episode.tokens_out)} out`}
              />
              <Stat
                label="Cost per episode"
                value={data.operations.tokens.cost > 0 ? count(data.operations.per_episode.cost) : "—"}
                hint={data.operations.tokens.cost > 0 ? undefined : "Set the price of a million tokens in Settings to see it."}
              />
              <Stat
                label="Wait before a job starts"
                value={duration(data.operations.wait_seconds.median)}
                hint={`longest tenth: ${duration(data.operations.wait_seconds.p90)} · ${count(data.operations.wait_seconds.jobs)} jobs`}
              />
              <Stat
                label="A call takes"
                value={millis(data.operations.latency_ms.median)}
                hint={`longest tenth: ${millis(data.operations.latency_ms.p90)}`}
              />
            </SimpleGrid>
          </Section>

          <Section title="Reliability" note="How often the provider said no, and how the strong model did.">
            <SimpleGrid cols={GRID}>
              <Stat label="Calls" value={count(data.operations.calls.total)} hint={`${count(data.operations.calls.failed)} did not return a usable answer`} />
              <Stat label="Too many requests (429)" value={percent(data.operations.calls.rate_429)} />
              <Stat label="Overloaded (503)" value={percent(data.operations.calls.rate_503)} />
              <Stat
                label="Moved to the strong model"
                value={percent(data.operations.escalation.rate)}
                hint={`${count(data.operations.escalation.escalated)} of ${count(data.operations.escalation.jobs)} jobs`}
              />
              <Stat
                label="Rescued by the strong model"
                value={percent(data.operations.escalation.rescue_rate)}
                hint={`${count(data.operations.escalation.rescued)} of ${count(data.operations.escalation.escalated)}`}
              />
            </SimpleGrid>
            {Object.keys(data.operations.calls.by_outcome).length > 0 && (
              <Text size="sm" c="dimmed">
                {Object.entries(data.operations.calls.by_outcome).map(([outcome, n]) => `${outcomeLabel(outcome)} ${count(n)}`).join(" · ")}
              </Text>
            )}
          </Section>

          <Section title="Day by day" note="Calls per day, UTC.">
            <Days days={data.daily} />
          </Section>
        </>
      )}

      <Section
        title="Your corrections"
        note="The cues you corrected are the golden set of a series: measure a prompt or a style against them with subcore compare --golden. Looking at them for a word you keep changing costs nothing."
      >
        {series.isPending && <Loader size="sm" aria-label="Loading the series" />}
        {series.data && series.data.items.length === 0 && <Text c="dimmed">The worker keeps no series yet.</Text>}
        <Stack gap="md" component="ul" aria-label="Series" style={{ margin: 0, padding: 0 }}>
          {(series.data?.items ?? []).map((row) => (
            <SeriesCorrections key={row.id} series={row} href={seriesRoute(catalog.data, row.id, "glossary")} />
          ))}
        </Stack>
      </Section>
    </Stack>
  );
}
