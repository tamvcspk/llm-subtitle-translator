import { Badge, Group, SimpleGrid, Text } from "@mantine/core";
import type { Insights } from "../../api/types";
import { against, count, dailyLimit, duration, millis, money, outcomeLabel, percent, tasksLine } from "../../lib/insights";
import { GRID, Section, Stat } from "./parts";

/** The queue, the models and today's calls, then what an episode takes in this window. */
export function QueueSection({ data, priced }: { data: Insights; priced: boolean }) {
  return (
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
  );
}

/** How often the provider said no, and how the strong model did. */
export function ReliabilitySection({ data }: { data: Insights }) {
  return (
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
  );
}
