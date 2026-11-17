import { Alert, Anchor, Button, Card, Drawer, Group, Select, Skeleton, Stack, Text, Title } from "@mantine/core";
import { IconChevronLeft } from "@tabler/icons-react";
import { useState } from "react";
import { Link, Outlet, useParams, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { useCatalog, useRemoveOutput, useUnit } from "../api/queries";
import type { UnitDetail, UnitTarget } from "../api/types";
import { RouteTabs } from "../components/RouteTabs";
import { languageName } from "../lib/home";
import { EPISODE_TABS, MOVIE_TABS, targetStatus, unitTitle, versionLine } from "../lib/unit";

/** What the tabs of one episode or movie share. */
export interface UnitContext {
  unit: UnitDetail;
  /** The language the tabs read; `null` when the unit has no target language at all. */
  target: UnitTarget | null;
}

function RemoveOutput({ unitId, target }: { unitId: number; target: UnitTarget }) {
  const [asking, setAsking] = useState(false);
  const remove = useRemoveOutput(unitId);
  return (
    <>
      <Button variant="default" color="red" onClick={() => setAsking(true)}>
        Remove output
      </Button>
      <Drawer opened={asking} onClose={() => setAsking(false)} position="bottom" size="auto" title="Remove subtitle output?" padding="lg" radius="lg">
        <Stack>
          <Text>This deletes the target subtitle file. The version stays in History and can be restored.</Text>
          {remove.isError && <Alert color="red">{remove.error.message}</Alert>}
          <Group justify="space-between">
            <Button variant="default" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button
              color="red"
              loading={remove.isPending}
              onClick={() => remove.mutate(target.lang, { onSuccess: () => setAsking(false) })}
            >
              Remove output
            </Button>
          </Group>
        </Stack>
      </Drawer>
    </>
  );
}

/** Header and tabs shared by an episode and a movie (wireframe 3); the tab itself is the outlet. */
export function Unit() {
  const { id } = useParams();
  const unitId = Number(id);
  const unit = useUnit(unitId);
  const catalog = useCatalog();
  const [params, setParams] = useSearchParams();

  if (unit.isPending) {
    return (
      <Stack gap="sm" aria-label="Loading">
        <Skeleton height={32} width="50%" />
        <Skeleton height={64} />
        <Skeleton height={44} />
      </Stack>
    );
  }
  if (unit.isError) {
    return (
      <Card withBorder padding="md">
        <Stack gap="xs">
          <Text>{unit.error instanceof ApiError && unit.error.status === 404 ? "That episode is not known to the worker." : "The episode could not be loaded."}</Text>
          <Text size="sm" c="dimmed">
            {unit.error.message}
          </Text>
          <Anchor component={Link} to="/library">
            Back to Library
          </Anchor>
        </Stack>
      </Card>
    );
  }

  const data = unit.data;
  const asked = params.get("lang");
  const target = data.targets.find((candidate) => candidate.lang === asked) ?? data.targets[0] ?? null;
  const catalogSeries = data.series ? catalog.data?.series.find((candidate) => candidate.series_id === data.series?.id) : undefined;
  const back = catalogSeries ? { to: `/series/${catalogSeries.id}/episodes`, label: catalogSeries.title } : { to: "/library", label: data.series?.title ?? "Library" };
  const line = target ? versionLine(target) : null;
  const when = target?.updated_at ? new Date(target.updated_at * 1000).toLocaleString() : null;

  const chooseLanguage = (value: string | null) => {
    if (!value) return;
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set("lang", value);
        return next;
      },
      { replace: true },
    );
  };

  return (
    <Stack gap="md">
      <Stack gap="xs">
        <Anchor component={Link} to={back.to} size="sm" style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
          <IconChevronLeft size={16} /> {back.label}
        </Anchor>
        <Title order={2}>{unitTitle(data)}</Title>
        {data.series_id === null && target && (
          <Anchor
            component={Link}
            to={`/unit/${data.id}/glossary?lang=${encodeURIComponent(target.lang)}`}
            style={{ display: "inline-flex", alignItems: "center", minHeight: 44, alignSelf: "flex-start" }}
          >
            Movie Glossary →
          </Anchor>
        )}
      </Stack>

      {data.targets.length > 1 && (
        <Select
          label="Target language"
          data={data.targets.map((item) => ({ value: item.lang, label: item.language || languageName(item.lang) }))}
          value={target?.lang ?? null}
          onChange={chooseLanguage}
          allowDeselect={false}
          style={{ maxWidth: 260 }}
        />
      )}

      {target ? (
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
          <Stack gap={2}>
            <Text fw={600}>
              {targetStatus(target)} · Missing units: {target.missing_units.length > 0 ? target.missing_units.join(", ") : "none"}
            </Text>
            {line && <Text size="sm">{line}</Text>}
            {when && (
              <Text size="sm" c="dimmed">
                {when}
              </Text>
            )}
            {target.modified_outside && (
              <Text size="sm" c="yellow.8">
                File changed outside the worker
              </Text>
            )}
            {!data.source_exists && (
              <Text size="sm" c="red.7">
                The source subtitle is no longer on disk.
              </Text>
            )}
          </Stack>
          {target.exists && target.version !== null && <RemoveOutput unitId={data.id} target={target} />}
        </Group>
      ) : (
        <Alert color="yellow">No target language is configured for this item.</Alert>
      )}

      <RouteTabs base={`/unit/${data.id}`} tabs={data.memory_applicable ? EPISODE_TABS : MOVIE_TABS} keep={["lang"]} />

      <Outlet context={{ unit: data, target } satisfies UnitContext} />
    </Stack>
  );
}
