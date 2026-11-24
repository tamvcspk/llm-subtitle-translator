import { Alert, Anchor, Badge, Box, Button, Card, Checkbox, Chip, Group, Stack, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import { Link, useOutletContext, useSearchParams } from "react-router";
import { useIgnore, useSeriesUnits, useStopJob } from "../api/queries";
import { EstimateSheet, type EstimateRequest } from "../components/EstimateSheet";
import {
  buildSeasons,
  filterSeasons,
  parseFilter,
  type EpisodeAction,
  type EpisodeRow,
  type Season,
  type StatusFilter,
} from "../lib/episodes";
import type { SeriesContext } from "./Series";

const FILTERS: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "missing", label: "Missing" },
  { value: "running", label: "Running" },
  { value: "failed", label: "Failed" },
  { value: "stale", label: "Stale" },
];

function ActionButton({
  action,
  code,
  busy,
  onEstimate,
  onStop,
  onUnignore,
}: {
  action: EpisodeAction;
  code: string;
  busy: boolean;
  onEstimate: (request: EstimateRequest) => void;
  onStop: (jobId: string) => void;
  onUnignore: (itemId: number) => void;
}) {
  const name = `${action.label} ${code}`;
  switch (action.kind) {
    case "link":
      return (
        <Button component={Link} to={action.to} variant="default" aria-label={name}>
          {action.label}
        </Button>
      );
    case "estimate":
      return (
        <Button variant="light" aria-label={name} onClick={() => onEstimate(action.request)}>
          {action.label}
        </Button>
      );
    case "stop":
      return (
        <Button variant="default" color="red" aria-label={name} loading={busy} onClick={() => onStop(action.jobId)}>
          {action.label}
        </Button>
      );
    case "unignore":
      return (
        <Button variant="default" aria-label={name} loading={busy} onClick={() => onUnignore(action.itemId)}>
          {action.label}
        </Button>
      );
  }
}

function EpisodeCard({
  row,
  selected,
  busy,
  onToggle,
  onEstimate,
  onStop,
  onUnignore,
}: {
  row: EpisodeRow;
  selected: boolean;
  busy: boolean;
  onToggle: () => void;
  onEstimate: (request: EstimateRequest) => void;
  onStop: (jobId: string) => void;
  onUnignore: (itemId: number) => void;
}) {
  return (
    <Card withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
      <Group align="flex-start" wrap="nowrap" gap="xs">
        <Box
          component="label"
          style={{
            minWidth: 44,
            minHeight: 44,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: row.gone ? "default" : "pointer",
            flexShrink: 0,
          }}
        >
          <Checkbox
            size="lg"
            aria-label={`Select ${row.code}`}
            checked={selected}
            disabled={row.gone}
            onChange={onToggle}
          />
        </Box>
        <Group justify="space-between" align="center" wrap="wrap" gap="sm" style={{ flex: 1, minWidth: 0 }}>
          <Stack gap={4} style={{ flex: "1 1 14rem", minWidth: 0 }}>
            <Group gap="xs" wrap="nowrap" align="center">
              <Text fw={600} style={{ flexShrink: 0 }}>
                {row.code}
              </Text>
              {row.to ? (
                <Anchor
                  component={Link}
                  to={row.to}
                  truncate
                  style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}
                >
                  {row.title}
                </Anchor>
              ) : (
                <Text truncate>{row.title}</Text>
              )}
            </Group>
            <Text size="sm">{row.label}</Text>
            {row.details.length > 0 && (
              <Text size="sm" c="dimmed">
                {row.details.join(" · ")}
              </Text>
            )}
            {row.warning && (
              <Badge color="yellow" variant="light" style={{ alignSelf: "flex-start" }}>
                {row.warning}
              </Badge>
            )}
          </Stack>
          <Group gap="xs" wrap="wrap">
            {row.actions.map((action) => (
              <ActionButton
                key={action.label}
                action={action}
                code={row.code}
                busy={busy}
                onEstimate={onEstimate}
                onStop={onStop}
                onUnignore={onUnignore}
              />
            ))}
          </Group>
        </Group>
      </Group>
    </Card>
  );
}

function SeasonHeader({
  season,
  selected,
  onToggle,
  onTranslate,
}: {
  season: Season;
  selected: number;
  onToggle: () => void;
  onTranslate: () => void;
}) {
  const all = season.rows.length > 0 && selected === season.rows.length;
  return (
    <Group justify="space-between" wrap="wrap" gap="xs" component="header">
      <Group gap="xs" wrap="nowrap">
        <Box
          component="label"
          style={{
            minWidth: 44,
            minHeight: 44,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            flexShrink: 0,
          }}
        >
          <Checkbox
            size="lg"
            aria-label={`Select all of ${season.title}`}
            checked={all}
            indeterminate={selected > 0 && !all}
            onChange={onToggle}
          />
        </Box>
        <Text fw={700} tt="uppercase" size="sm" c="dimmed">
          {season.title}
        </Text>
      </Group>
      {season.season !== null && (
        <Button variant="light" disabled={season.askable === 0} onClick={onTranslate}>
          Translate {season.title.toLowerCase()} ({season.askable} missing)
        </Button>
      )}
    </Group>
  );
}

/** Episodes tab (wireframe 2a): one row per episode with its label and its one button; Translate by season, series or selection. */
export function Episodes() {
  const { page, lang } = useOutletContext<SeriesContext>();
  const hasActiveJob = useMemo(
    () => page.items.some((item) => item.targets.some((t) => t.job === "running" || t.job === "requested" || t.job === "waiting")),
    [page.items],
  );
  const units = useSeriesUnits(page.series.series_id, hasActiveJob);
  const stop = useStopJob();
  const ignore = useIgnore();
  const [params, setParams] = useSearchParams();
  const filter = parseFilter(params.get("status"));
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [estimating, setEstimating] = useState<EstimateRequest | null>(null);

  const seasons = useMemo(() => buildSeasons(page, units.data?.units, lang), [page, units.data, lang]);
  const shown = useMemo(() => filterSeasons(seasons, filter), [seasons, filter]);
  const visible = useMemo(() => shown.flatMap((season) => season.rows).filter((row) => !row.gone), [shown]);
  const chosen = visible.filter((row) => selected.has(row.id)).map((row) => row.id);
  const askable = seasons.reduce((sum, season) => sum + season.askable, 0);
  const title = page.series.title;

  const toggle = (id: number) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const toggleSeason = (season: Season) =>
    setSelected((previous) => {
      const ids = season.rows.filter((row) => !row.gone).map((row) => row.id);
      const next = new Set(previous);
      if (ids.every((id) => next.has(id))) ids.forEach((id) => next.delete(id));
      else ids.forEach((id) => next.add(id));
      return next;
    });
  const setFilter = (value: StatusFilter) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value === "all") next.delete("status");
        else next.set("status", value);
        return next;
      },
      { replace: true },
    );

  const busy = stop.isPending || ignore.isPending;
  const failure = stop.error ?? ignore.error;

  return (
    <Stack gap="md">
      <Card withBorder padding="sm" radius="md">
        <Stack gap="xs">
          <Group justify="space-between" wrap="wrap" gap="xs">
            <Text fw={600}>Selected {chosen.length}</Text>
            <Group gap="xs" wrap="wrap">
              <Button
                variant="light"
                disabled={chosen.length === 0}
                onClick={() =>
                  setEstimating({
                    action: `Translate selected · ${chosen.length} ${chosen.length === 1 ? "episode" : "episodes"}`,
                    selector: { items: chosen, languages: [lang], mode: "auto" },
                  })
                }
              >
                Translate selected
              </Button>
              <Button
                variant="default"
                disabled={chosen.length === 0}
                loading={ignore.isPending}
                onClick={() =>
                  ignore.mutate(
                    { ignore: true, items: chosen, languages: [lang] },
                    { onSuccess: () => setSelected(new Set()) },
                  )
                }
              >
                Ignore selected
              </Button>
            </Group>
          </Group>
          <Button
            variant="light"
            disabled={askable === 0}
            onClick={() =>
              setEstimating({
                action: `Translate whole series · ${title}`,
                selector: { series: page.series.id, languages: [lang] },
              })
            }
          >
            Translate whole series ({askable} missing)
          </Button>
        </Stack>
      </Card>

      <Chip.Group multiple={false} value={filter} onChange={(value) => setFilter(value as StatusFilter)}>
        <Group gap="xs" role="group" aria-label="Filter">
          {FILTERS.map((item) => (
            <Chip key={item.value} value={item.value} size="md">
              {item.label}
            </Chip>
          ))}
        </Group>
      </Chip.Group>

      {failure && <Alert color="red">{failure.message}</Alert>}
      {units.isError && <Alert color="yellow">Versions could not be loaded: {units.error.message}</Alert>}

      {seasons.length === 0 ? (
        <Card withBorder padding="md">
          <Text>No episodes of this series are in the catalog yet.</Text>
        </Card>
      ) : shown.length === 0 ? (
        <Card withBorder padding="md">
          <Group justify="space-between">
            <Text>No episodes are {FILTERS.find((item) => item.value === filter)?.label.toLowerCase()} here.</Text>
            <Button variant="default" onClick={() => setFilter("all")}>
              Clear filter
            </Button>
          </Group>
        </Card>
      ) : (
        shown.map((season) => (
          <Stack key={season.season ?? "other"} gap="xs" component="section" aria-label={season.title}>
            <SeasonHeader
              season={season}
              selected={season.rows.filter((row) => selected.has(row.id)).length}
              onToggle={() => toggleSeason(season)}
              onTranslate={() =>
                setEstimating({
                  action: `Translate ${season.title.toLowerCase()} · ${title}`,
                  selector: { series: page.series.id, season: season.season ?? 0, languages: [lang] },
                })
              }
            />
            <Stack component="ul" gap="xs" m={0} p={0}>
              {season.rows.map((row) => (
                <EpisodeCard
                  key={row.id}
                  row={row}
                  selected={selected.has(row.id)}
                  busy={busy}
                  onToggle={() => toggle(row.id)}
                  onEstimate={setEstimating}
                  onStop={(jobId) => stop.mutate(jobId)}
                  onUnignore={(itemId) => ignore.mutate({ ignore: false, items: [itemId], languages: [lang] })}
                />
              ))}
            </Stack>
          </Stack>
        ))
      )}

      <EstimateSheet request={estimating} onClose={() => setEstimating(null)} />
    </Stack>
  );
}
