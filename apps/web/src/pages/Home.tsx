import { Anchor, Badge, Button, Card, Chip, Group, Skeleton, Stack, Text, TextInput } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useCatalog, useMovies, useSyncCatalog } from "../api/queries";
import { EstimateSheet, type EstimateRequest } from "../components/EstimateSheet";
import { buildRows, filterRows, languageName, type Filter, type Row } from "../lib/home";
import { readHomeScroll, saveHomeScroll } from "../lib/navigation";

const VALID_FILTERS = new Set<string>(["all", "missing", "running", "failed", "ignored"]);

const FILTERS: Array<{ value: Filter; label: (language: string) => string }> = [
  { value: "all", label: () => "All" },
  { value: "missing", label: (language) => `Missing${language ? ` ${language}` : ""}` },
  { value: "running", label: () => "Running" },
  { value: "failed", label: () => "Failed" },
  { value: "ignored", label: () => "Ignored" },
];

function RowCard({ row, onTranslate }: { row: Row; onTranslate: (request: EstimateRequest) => void }) {
  const { action } = row;
  return (
    <Card withBorder padding="md" radius="md" component="li" style={{ listStyle: "none" }}>
      <Group justify="space-between" align="center" wrap="wrap" gap="sm">
        <Stack gap={4} style={{ flex: "1 1 14rem", minWidth: 0 }}>
          <Group gap="xs" wrap="nowrap">
            <Badge variant="light" color={row.kind === "TV" ? "teal" : "grape"} aria-label={row.kind === "TV" ? "Series" : "Movie"}>
              {row.kind}
            </Badge>
            {row.to ? (
              <Anchor component={Link} to={row.to} fw={600} truncate style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
                {row.title}
              </Anchor>
            ) : (
              <Text fw={600} truncate>
                {row.title}
              </Text>
            )}
          </Group>
          <Text size="sm" c="dimmed">
            {row.facts.join(" · ")}
          </Text>
        </Stack>
        {action.kind === "view-failed" && (
          <Button component={Link} to={action.to} variant="light" color="red">
            View failed
          </Button>
        )}
        {action.kind === "translate" && (
          <Button
            variant="light"
            onClick={() => onTranslate({ action: `Translate missing · ${action.title}`, selector: action.selector })}
          >
            Translate missing
          </Button>
        )}
        {action.kind === "improve" && (
          <Button component={Link} to={action.to} variant="default">
            Improve
          </Button>
        )}
      </Group>
    </Card>
  );
}

/** Home: ask by name, not by queue (UX §3 rule 1). Search & filter live in URL (`?q=&filter=`) and scroll restores (P18e). */
export function Home() {
  const catalog = useCatalog();
  const movies = useMovies();
  const sync = useSyncCatalog();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const rawFilter = params.get("filter") ?? "all";
  const filter: Filter = VALID_FILTERS.has(rawFilter) ? (rawFilter as Filter) : "all";
  const [estimating, setEstimating] = useState<EstimateRequest | null>(null);
  const restored = useRef(false);

  const rows = useMemo(() => buildRows(catalog.data, movies.data?.items), [catalog.data, movies.data]);
  const shown = useMemo(() => filterRows(rows, query, filter), [rows, query, filter]);
  const languages = catalog.data?.languages ?? [];
  const language = languages.length === 1 && languages[0] ? languageName(languages[0]) : "";
  const loading = catalog.isPending || movies.isPending;
  const failed = catalog.isError || movies.isError;

  useEffect(() => {
    const onScroll = () => {
      if (restored.current) saveHomeScroll(window.scrollY);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      if (restored.current) saveHomeScroll(window.scrollY);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  useEffect(() => {
    if (loading || restored.current) return;
    restored.current = true;
    const saved = readHomeScroll();
    if (saved > 0) window.scrollTo(0, saved);
  }, [loading]);

  const setQuery = (nextQuery: string) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (nextQuery) next.set("q", nextQuery);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );

  const setFilter = (nextFilter: Filter) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (nextFilter === "all") next.delete("filter");
        else next.set("filter", nextFilter);
        return next;
      },
      { replace: true },
    );

  const clear = () =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("q");
        next.delete("filter");
        return next;
      },
      { replace: true },
    );

  return (
    <Stack gap="md">
      <Group justify="space-between" wrap="nowrap" align="flex-end">
        <TextInput
          style={{ flex: 1 }}
          aria-label="Search by title"
          placeholder="Search by title…"
          leftSection={<IconSearch size={18} />}
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
        />
        <Button variant="default" loading={sync.isPending} onClick={() => sync.mutate()}>
          Sync now
        </Button>
      </Group>
      <Chip.Group multiple={false} value={filter} onChange={(value) => setFilter(value as Filter)}>
        <Group gap="xs" role="group" aria-label="Filter">
          {FILTERS.map((item) => (
            <Chip key={item.value} value={item.value} size="md">
              {item.label(language)}
            </Chip>
          ))}
        </Group>
      </Chip.Group>

      {loading && !failed ? (
        <Stack gap="sm" aria-label="Loading">
          {[0, 1, 2, 3, 4].map((n) => (
            <Skeleton key={n} height={72} radius="md" />
          ))}
        </Stack>
      ) : failed ? (
        <Card withBorder padding="md">
          <Stack gap="xs">
            <Text>The library could not be loaded.</Text>
            <Text size="sm" c="dimmed">
              {(catalog.error ?? movies.error)?.message}
            </Text>
          </Stack>
        </Card>
      ) : rows.length === 0 ? (
        <Card withBorder padding="md">
          <Stack gap="sm">
            <Text fw={600}>No series or movies in the catalog yet.</Text>
            <Text size="sm" c="dimmed">
              Check the Bazarr connection, or sync now.
            </Text>
            <Group justify="space-between">
              <Button variant="default" onClick={() => void navigate("/settings")}>
                Open Connections
              </Button>
              <Button loading={sync.isPending} onClick={() => sync.mutate()}>
                Sync now
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : shown.length === 0 ? (
        <Card withBorder padding="md">
          <Group justify="space-between">
            <Text>
              No items match{query.trim() ? ` "${query.trim()}"` : ""} in {FILTERS.find((item) => item.value === filter)?.label(language)}.
            </Text>
            <Button variant="default" onClick={clear}>
              Clear filter
            </Button>
          </Group>
        </Card>
      ) : (
        <Stack component="ul" gap="sm" m={0} p={0} aria-label="Library">
          {shown.map((row) => (
            <RowCard key={row.key} row={row} onTranslate={setEstimating} />
          ))}
        </Stack>
      )}

      <EstimateSheet request={estimating} onClose={() => setEstimating(null)} />
    </Stack>
  );
}
