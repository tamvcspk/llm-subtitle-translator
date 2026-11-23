import { Alert, Anchor, Badge, Button, Card, Group, Loader, SegmentedControl, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useCatalog, useFromEdits, useInsights, useLibrarySeries, useSeriesUnits } from "../api/queries";
import type { CorrectionCount, InsightsAb, InsightsBreakdown, InsightsDay, LibrarySeriesRow } from "../api/types";
import { GoldenSheet, type GoldenTarget } from "../components/GoldenSheet";
import {
  abLabel,
  against,
  barWidth,
  count,
  DAY_CHOICES,
  dailyLimit,
  dayEditRate,
  duration,
  editsOf,
  fromEditsLine,
  millis,
  money,
  outcomeLabel,
  percent,
  reviewLabel,
  tasksLine,
  tooFewToConclude,
  trend,
  AB_ENOUGH,
  type Trend,
} from "../lib/insights";
import { seriesRoute } from "../lib/seriesLink";

const CORRECTIONS_ID = "your-corrections";

/** From this width the lists of figures are tables, below it cards (wireframe 5b, UX §8). */
function useWide(): boolean {
  return useMediaQuery("(min-width: 1024px)") ?? false;
}

function Section({ title, note, id, children }: { title: string; note?: string; id?: string; children: ReactNode }) {
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label={title} id={id}>
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

const TREND_COLOR = { better: "teal", worse: "red", same: "dimmed", none: "dimmed" } as const;

function Stat({ label, value, hint, trend }: { label: string; value: ReactNode; hint?: ReactNode; trend?: Trend | null }) {
  return (
    <Stack gap={2} role="group" aria-label={label}>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text fw={700} size="xl">
        {value}
      </Text>
      {trend && (
        <Text size="xs" c={TREND_COLOR[trend.tone]} fw={trend.tone === "better" || trend.tone === "worse" ? 600 : undefined}>
          {trend.text}
        </Text>
      )}
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Stack>
  );
}

const GRID = { base: 1, xs: 2, md: 3 } as const;

/** `gemini-3.1-flash-lite · prompt v4`: a model with the prompt version it wrote with. */
function pairName(row: InsightsBreakdown): string {
  const model = row.model || "(model not recorded)";
  return row.prompt_version ? `${model} · prompt ${row.prompt_version}` : model;
}

const pairKey = (row: InsightsBreakdown) => `${row.model ?? ""}|${row.prompt_version ?? ""}`;

/** One model with one prompt version, as a card (below 1024 px; the table of the same figures is `PairTable`). */
function PairCard({ row }: { row: InsightsBreakdown }) {
  return (
    <Card withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
      <Stack gap="xs">
        <Text fw={600}>{pairName(row)}</Text>
        <SimpleGrid cols={{ base: 2, md: 5 }} spacing="xs">
          <Stat label="Episodes" value={count(row.episodes)} hint={`${count(row.cues)} cues`} />
          <Stat label="Cues you corrected" value={percent(row.cue_edit_rate)} hint={`${count(row.cue_edits)} cues`} />
          <Stat label="Repairs" value={percent(row.repair_rate)} hint="of translate calls" />
          <Stat label="Read too fast" value={percent(row.cps_rate)} />
          <Stat label="Lines too long" value={percent(row.too_long_rate)} />
        </SimpleGrid>
      </Stack>
    </Card>
  );
}

/** A table that scrolls on its own when it is wider than the page, never the page. */
function Grid({ label, head, children }: { label: string; head: string[]; children: ReactNode }) {
  return (
    <Table.ScrollContainer minWidth={560}>
      <Table aria-label={label} verticalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            {head.map((name, i) => (
              <Table.Th key={name} ta={i === 0 ? "left" : "right"}>
                {name}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>{children}</Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function PairTable({ rows }: { rows: InsightsBreakdown[] }) {
  return (
    <Grid label="By model and prompt version" head={["Model", "Prompt", "Episodes", "Cues you corrected", "Repairs", "Read too fast", "Lines too long"]}>
      {rows.map((row) => (
        <Table.Tr key={pairKey(row)}>
          <Table.Td fw={600}>{row.model || "(not recorded)"}</Table.Td>
          <Table.Td ta="right">{row.prompt_version || "—"}</Table.Td>
          <Table.Td ta="right">{count(row.episodes)}</Table.Td>
          <Table.Td ta="right">{`${percent(row.cue_edit_rate)} (${count(row.cue_edits)})`}</Table.Td>
          <Table.Td ta="right">{percent(row.repair_rate)}</Table.Td>
          <Table.Td ta="right">{percent(row.cps_rate)}</Table.Td>
          <Table.Td ta="right">{percent(row.too_long_rate)}</Table.Td>
        </Table.Tr>
      ))}
    </Grid>
  );
}

function Pairs({ rows }: { rows: InsightsBreakdown[] }) {
  const wide = useWide();
  if (rows.length === 0) return <Text c="dimmed">Nothing has been translated in this window.</Text>;
  if (wide) return <PairTable rows={rows} />;
  return (
    <Stack gap="sm" component="ul" aria-label="By model and prompt version" style={{ margin: 0, padding: 0 }}>
      {rows.map((row) => (
        <PairCard key={pairKey(row)} row={row} />
      ))}
    </Stack>
  );
}

/** One style and prompt version: what the admin chose between the variant and the current. Dimmed until it means something. */
function AbRow({ row }: { row: InsightsAb }) {
  const few = tooFewToConclude(row);
  return (
    <Stack gap={2} component="li" style={{ listStyle: "none" }}>
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Text fw={600}>{abLabel(row)}</Text>
        <Text size="sm" c={few ? "dimmed" : undefined} fw={few ? undefined : 700}>
          Score {percent(row.score)} · {count(row.variant)} for the variant, {count(row.current)} for the current, {count(row.tie)} ties
        </Text>
      </Group>
      {few && (
        <Text size="sm" c="dimmed">
          {`Too few comparisons to conclude (${count(row.judged)} of ${AB_ENOUGH} needed)`}
        </Text>
      )}
    </Stack>
  );
}

function AbTable({ rows }: { rows: InsightsAb[] }) {
  return (
    <Grid label="Style trials" head={["Style", "Compared", "Variant", "Current", "Ties", "Score"]}>
      {rows.map((row) => {
        const few = tooFewToConclude(row);
        return (
          <Table.Tr key={`${row.style}|${row.prompt_version}`}>
            <Table.Td fw={600}>
              {abLabel(row)}
              {few && (
                <Text size="xs" c="dimmed">
                  {`Too few comparisons to conclude (needs ${AB_ENOUGH})`}
                </Text>
              )}
            </Table.Td>
            <Table.Td ta="right">{count(row.judged)}</Table.Td>
            <Table.Td ta="right">{count(row.variant)}</Table.Td>
            <Table.Td ta="right">{count(row.current)}</Table.Td>
            <Table.Td ta="right">{count(row.tie)}</Table.Td>
            <Table.Td ta="right" c={few ? "dimmed" : undefined} fw={few ? undefined : 700}>
              {percent(row.score)}
            </Table.Td>
          </Table.Tr>
        );
      })}
    </Grid>
  );
}

function StyleTrials({ rows }: { rows: InsightsAb[] }) {
  const wide = useWide();
  if (rows.length === 0) return <Text c="dimmed">No variant has been judged cue by cue yet.</Text>;
  if (wide) return <AbTable rows={rows} />;
  return (
    <Stack gap="sm" component="ul" aria-label="Style trials" style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {rows.map((row) => (
        <AbRow key={`${row.style}|${row.prompt_version}`} row={row} />
      ))}
    </Stack>
  );
}

/** The bar of a day against the day of the window with the most episodes (no CDN, two divs). */
function Bar({ day, days }: { day: InsightsDay; days: InsightsDay[] }) {
  return (
    <div aria-hidden style={{ flex: 1, minWidth: 0, background: "var(--mantine-color-default-border)", borderRadius: 4, height: 8 }}>
      <div style={{ width: `${barWidth(day, days)}%`, height: 8, borderRadius: 4, background: "var(--mantine-primary-color-filled)" }} />
    </div>
  );
}

/** `12 eps · 0 failed · edit 3.8 % · 0.16`: what a day came to, in the words of the wireframe. */
function dayLine(day: InsightsDay, priced: boolean): string {
  const parts = [`${count(day.episodes)} eps`, `${count(day.failed)} failed`, `edit ${percent(dayEditRate(day))}`];
  if (priced) parts.push(money(day.cost));
  return parts.join(" · ");
}

function Days({ days, priced }: { days: InsightsDay[]; priced: boolean }) {
  const wide = useWide();
  const newest = [...days].reverse();
  if (wide) {
    return (
      <Table.ScrollContainer minWidth={560}>
        <Table aria-label="Days" verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Date</Table.Th>
              <Table.Th w="40%">
                <Text span size="xs" c="dimmed" fw={400}>
                  bar = episodes that day
                </Text>
              </Table.Th>
              <Table.Th ta="right">Episodes</Table.Th>
              <Table.Th ta="right">Failed calls</Table.Th>
              <Table.Th ta="right">Cues you corrected</Table.Th>
              {priced && <Table.Th ta="right">Cost</Table.Th>}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {newest.map((day) => (
              <Table.Tr key={day.day}>
                <Table.Td>{day.day}</Table.Td>
                <Table.Td>
                  <Bar day={day} days={days} />
                </Table.Td>
                <Table.Td ta="right">{count(day.episodes)}</Table.Td>
                <Table.Td ta="right">{count(day.failed)}</Table.Td>
                <Table.Td ta="right">{percent(dayEditRate(day))}</Table.Td>
                {priced && <Table.Td ta="right">{money(day.cost)}</Table.Td>}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    );
  }
  return (
    <Stack gap="xs" component="ul" aria-label="Days" style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {newest.map((day) => (
        <Stack key={day.day} component="li" gap={2}>
          <Group wrap="nowrap" gap="sm" align="center" style={{ minHeight: 28 }}>
            <Text size="sm" w={86} style={{ flex: "none" }}>
              {day.day}
            </Text>
            <Bar day={day} days={days} />
          </Group>
          <Text size="xs" c="dimmed">
            {dayLine(day, priced)}
          </Text>
        </Stack>
      ))}
    </Stack>
  );
}

/** The golden set and the look for repeated corrections of one series, one row per language it has. */
function SeriesCorrections({
  series,
  href,
  counts,
  onGolden,
}: {
  series: LibrarySeriesRow;
  href: string | null;
  counts: readonly CorrectionCount[];
  onGolden: (target: GoldenTarget) => void;
}) {
  const languages = useSeriesUnits(series.id).data?.languages ?? [];
  const look = useFromEdits();
  const [said, setSaid] = useState<{ lang: string; line: string } | null>(null);
  if (languages.length === 0) return null;
  return (
    <Card withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }} aria-label={series.title}>
      <Stack gap="xs">
        {href ? (
          <Anchor component={Link} to={href} fw={600}>
            {series.title}
          </Anchor>
        ) : (
          <Text fw={600}>{series.title}</Text>
        )}
        {languages.map((lang) => {
          const edits = editsOf(counts, series.id, lang);
          return (
          <Group key={lang} gap="sm" wrap="wrap">
            <Badge variant="light" size="lg" radius="sm" tt="none">
              {`${lang} · ${count(edits)} edits`}
            </Badge>
            <Button
              variant="default"
              disabled={edits === 0}
              loading={look.isPending && look.variables?.lang === lang && look.variables.seriesId === series.id}
              onClick={() =>
                look.mutate({ seriesId: series.id, lang }, { onSuccess: (answer) => setSaid({ lang, line: fromEditsLine(answer) }) })
              }
              aria-label={`Look at my ${lang} edits of ${series.title}`}
            >
              Look at my edits
            </Button>
            <Button
              variant="default"
              disabled={edits === 0}
              onClick={() => onGolden({ seriesId: series.id, seriesKey: series.key, title: series.title, lang })}
              aria-label={`Open the ${lang} golden set of ${series.title}`}
            >
              Golden set
            </Button>
          </Group>
          );
        })}
        {said && <Text size="sm" role="status">{`${said.lang}: ${said.line}`}</Text>}
        {look.isError && <Alert color="red">{look.error.message}</Alert>}
      </Stack>
    </Card>
  );
}

/**
 * Insights (FR-19.5, UX A.6b, wireframe 5b): how the worker is doing and how good its translations are, worked out from its own
 * records. The figure that matters most is the share of cues you had to correct, by model and prompt version: measure before you
 * change a prompt or a style, then again after.
 */
export function Insights() {
  const [days, setDays] = useState<number>(30);
  const [golden, setGolden] = useState<GoldenTarget | null>(null);
  const insights = useInsights(days);
  const series = useLibrarySeries();
  const catalog = useCatalog();
  const data = insights.data;
  const priced = (data?.operations.tokens.cost ?? 0) > 0;
  const corrections = data?.corrections ?? [];

  return (
    <Stack gap="md">
      <Stack gap={2}>
        <Group justify="space-between" align="center" wrap="wrap" gap="xs">
          <Title order={2}>Insights</Title>
          <Badge variant="light" color="gray" size="lg" radius="sm" tt="none">
            read-only · no model calls
          </Badge>
        </Group>
        <Text c="dimmed">Worked out from what the worker recorded. Nothing here costs a model call.</Text>
      </Stack>
      <Group justify="space-between" align="center" wrap="wrap" gap="sm">
        <SegmentedControl
          aria-label="Window"
          value={String(days)}
          onChange={(value) => setDays(Number(value))}
          data={DAY_CHOICES.map((choice) => ({ value: String(choice), label: `${choice}d` }))}
          styles={{ label: { minHeight: 44, display: "flex", alignItems: "center" } }}
        />
        <Button
          variant="subtle"
          onClick={() => document.getElementById(CORRECTIONS_ID)?.scrollIntoView({ behavior: "smooth", block: "start" })}
        >
          Your corrections ↓
        </Button>
      </Group>

      {insights.isPending && <Loader size="sm" aria-label="Loading the figures" />}
      {insights.isError && <Alert color="red">{insights.error.message}</Alert>}

      {data && (
        <>
          <Section
            title="Quality"
            note={`What you had to fix, and what the review found, over the last ${days} days. ▼ is better and ▲ worse than the ${days} days before.`}
          >
            <SimpleGrid cols={GRID}>
              <Stat
                label="Cues you corrected"
                value={percent(data.quality.cue_edits.rate)}
                trend={trend(data.quality.cue_edits.rate, data.previous.cue_edit_rate)}
                hint={`${count(data.quality.cue_edits.cues)} of ${count(data.quality.cues_translated)} cues translated`}
              />
              <Stat
                label="Repairs"
                value={percent(data.quality.repair.rate)}
                trend={trend(data.quality.repair.rate, data.previous.repair_rate)}
                hint={`${count(data.quality.repair.calls)} repair or split calls of ${count(data.quality.repair.translate_calls)}`}
              />
              <Stat
                label="Retranslated"
                value={percent(data.quality.retranslations.rate)}
                trend={trend(data.quality.retranslations.rate, data.previous.retranslate_rate)}
                hint={`${count(data.quality.retranslations.count)} of ${count(data.quality.episodes)} episodes; ${count(data.quality.retranslations.reverts)} reverted`}
              />
              <Stat
                label="Read too fast"
                value={percent(data.quality.review.cps_rate)}
                trend={trend(data.quality.review.cps_rate, data.previous.cps_rate)}
                hint={`${count(data.quality.review.cps)} cues over 20 characters a second`}
              />
              <Stat
                label="Lines too long"
                value={percent(data.quality.review.too_long_rate)}
                trend={trend(data.quality.review.too_long_rate, data.previous.too_long_rate)}
                hint={`${count(data.quality.review.too_long)} cues over 42 characters a line`}
              />
            </SimpleGrid>
            {Object.keys(data.quality.review.by_kind).length > 0 && (
              <Text size="sm" c="dimmed">
                Review found: {Object.entries(data.quality.review.by_kind).map(([kind, n]) => `${reviewLabel(kind)} ${count(n)}`).join(" · ")}
              </Text>
            )}
          </Section>

          <Section
            title="By model and prompt version"
            note="Measure before you change a prompt or a style, then again after: a model you corrected more often is the weaker one for your shows, and the prompt version that wrote a cue is blamed for your correction."
          >
            <Pairs rows={data.by_model_prompt} />
          </Section>

          <Section
            title="Style A/B scores"
            note={`Your verdicts on variants, added up by style and prompt version. A win counts one, a tie a half. Under ${AB_ENOUGH} comparisons a score is not a conclusion.`}
          >
            <StyleTrials rows={data.ab} />
          </Section>

          <Section title="Queue, quota and cost" note="Live: the queue, the models and today's calls; then what an episode takes in this window.">
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
              <Stat label="Episodes translated" value={count(data.operations.per_episode.episodes)} />
              <Stat
                label="Tokens per episode"
                value={`${count(data.operations.per_episode.tokens_in)} in`}
                hint={`${count(data.operations.per_episode.tokens_out)} out`}
              />
              <Stat
                label="Cost per episode"
                value={priced ? money(data.operations.per_episode.cost) : "—"}
                hint={priced ? undefined : "Set the price of a million tokens in Settings to see it."}
              />
              <Stat
                label="Quota pauses"
                value={count(data.operations.quota_pauses)}
                hint="days the daily quota stopped the queue"
              />
              <Stat label="Calls by task" value={tasksLine(data.operations.calls_by_task)} />
              <Stat
                label="Research cost"
                value={data.operations.calls_by_task.research ? (priced ? money(data.operations.research_cost) : "—") : "—"}
                hint={data.operations.calls_by_task.research ? `${count(data.operations.calls_by_task.research)} research calls` : "No research yet."}
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
              <Stat
                label="Job success rate"
                value={percent(data.operations.jobs.success_rate)}
                hint={`${count(data.operations.jobs.done)} done, ${count(data.operations.jobs.failed)} failed`}
              />
              <Stat
                label="Retries per episode"
                value={data.operations.jobs.retries_per_episode === null ? "—" : data.operations.jobs.retries_per_episode.toFixed(2)}
                hint="times a job waited because the provider said no"
              />
              <Stat label="Breaker opens" value={count(data.operations.breaker_opens)} hint="a model rested after repeated failures" />
            </SimpleGrid>
            {Object.keys(data.operations.calls.by_outcome).length > 0 && (
              <Text size="sm" c="dimmed">
                {Object.entries(data.operations.calls.by_outcome).map(([outcome, n]) => `${outcomeLabel(outcome)} ${count(n)}`).join(" · ")}
              </Text>
            )}
          </Section>

          <Section title="Daily" note="Episodes translated per day, UTC; a full bar is the busiest day of the window.">
            <Days days={data.daily} priced={priced} />
          </Section>
        </>
      )}

      <Section
        id={CORRECTIONS_ID}
        title="Your corrections"
        note="Find repeated edit patterns to propose to Review, or open a golden set: the cues you corrected, to measure a prompt or a style against with subcore compare --golden. Looking costs nothing."
      >
        {series.isPending && <Loader size="sm" aria-label="Loading the series" />}
        {series.data && series.data.items.length === 0 && <Text c="dimmed">The worker keeps no series yet.</Text>}
        <SimpleGrid cols={{ base: 1, md: 2 }} component="ul" aria-label="Series" style={{ margin: 0, padding: 0 }}>
          {[...(series.data?.items ?? [])]
            .sort((a, b) => editsOf(corrections, b.id) - editsOf(corrections, a.id) || a.title.localeCompare(b.title))
            .map((row) => (
              <SeriesCorrections key={row.id} series={row} href={seriesRoute(catalog.data, row.id, "glossary")} counts={corrections} onGolden={setGolden} />
            ))}
        </SimpleGrid>
      </Section>

      <GoldenSheet target={golden} onClose={() => setGolden(null)} />
    </Stack>
  );
}
