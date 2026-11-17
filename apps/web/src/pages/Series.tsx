import { Alert, Anchor, Button, Card, Group, SegmentedControl, Select, Skeleton, Stack, Text, Title } from "@mantine/core";
import { IconChevronLeft } from "@tabler/icons-react";
import { Link, Outlet, useParams, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { useReleaseBootstrap, useSeriesPage, useSetAutoRequest } from "../api/queries";
import type { SeriesPage } from "../api/types";
import { MemoryControls } from "../components/MemoryControls";
import { RouteTabs } from "../components/RouteTabs";
import { languageName } from "../lib/home";

/** What the tabs of one series share: the page the header already loaded, and the target language it is read in. */
export interface SeriesContext {
  page: SeriesPage;
  lang: string;
}

const TABS = [
  { value: "episodes", label: "Episodes" },
  { value: "style", label: "Style" },
  { value: "glossary", label: "Glossary" },
  { value: "characters", label: "Characters" },
  { value: "context", label: "Context" },
] as const;

/** "Default" follows the global `auto_request` setting; the API says that with an empty string. */
const POLICY = [
  { value: "default", label: "Default" },
  { value: "on", label: "On" },
  { value: "off", label: "Off" },
];

/** Header common to the five tabs (wireframe 2): title, target language, `auto_request`. Memory and Specials after join with the Context tab. */
export function Series() {
  const { id } = useParams();
  const seriesId = Number(id);
  const page = useSeriesPage(seriesId);
  const policy = useSetAutoRequest(seriesId);
  const release = useReleaseBootstrap(seriesId);
  const [params, setParams] = useSearchParams();

  if (page.isPending) {
    return (
      <Stack gap="sm" aria-label="Loading">
        <Skeleton height={32} width="40%" />
        <Skeleton height={44} />
        <Skeleton height={72} radius="md" />
      </Stack>
    );
  }
  if (page.isError) {
    return (
      <Card withBorder padding="md">
        <Stack gap="xs">
          <Text>{page.error instanceof ApiError && page.error.status === 404 ? "That series is not in the catalog." : "The series could not be loaded."}</Text>
          <Text size="sm" c="dimmed">
            {page.error.message}
          </Text>
          <Anchor component={Link} to="/library">
            Back to Library
          </Anchor>
        </Stack>
      </Card>
    );
  }

  const { series, languages } = page.data;
  const asked = params.get("lang");
  const lang = asked && languages.includes(asked) ? asked : (languages[0] ?? "");
  const context: SeriesContext = { page: page.data, lang };

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
        <Anchor component={Link} to="/library" size="sm" style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
          <IconChevronLeft size={16} /> Library
        </Anchor>
        <Title order={2}>{series.title}</Title>
        {series.gone && <Alert color="yellow">Bazarr no longer lists this series. What the worker kept is shown.</Alert>}
      </Stack>

      <Group align="flex-end" gap="lg" wrap="wrap">
        {languages.length > 1 && (
          <Select
            label="Target language"
            data={languages.map((code) => ({ value: code, label: languageName(code) }))}
            value={lang}
            onChange={chooseLanguage}
            allowDeselect={false}
            style={{ minWidth: 180 }}
          />
        )}
        <Stack gap={4}>
          <Text size="sm" fw={500} id="auto-request-label">
            Auto-request
          </Text>
          <SegmentedControl
            aria-labelledby="auto-request-label"
            data={POLICY}
            value={series.auto_request || "default"}
            disabled={policy.isPending}
            onChange={(value) => policy.mutate(value === "default" ? "" : (value as "on" | "off"))}
          />
        </Stack>
      </Group>
      {policy.isError && <Alert color="red">{policy.error.message}</Alert>}
      {series.bootstrap.locked && (
        <Alert color="yellow" title="Auto-request is waiting for this series' glossary" role="status">
          <Stack gap="xs">
            <Text size="sm">
              Nothing is approved for {series.title} yet, so only its first {series.bootstrap.limit} episode
              {series.bootstrap.limit === 1 ? " was" : "s were"} requested. {series.bootstrap.held} more wait
              {series.bootstrap.held === 1 ? "s" : ""} until the suggestions are reviewed
              {series.bootstrap.pending > 0 ? ` (${series.bootstrap.pending} waiting)` : ""}. You can still translate any episode yourself.
            </Text>
            <Group gap="xs" wrap="wrap">
              {series.bootstrap.pending > 0 && (
                <Button component={Link} to="/review" variant="light">
                  Review suggestions
                </Button>
              )}
              <Button variant="default" loading={release.isPending} onClick={() => release.mutate(true)}>
                Continue
              </Button>
            </Group>
            <Text size="xs" c="dimmed">
              Continue lets the rest through at the next sync.
            </Text>
          </Stack>
        </Alert>
      )}
      {release.isError && <Alert color="red">{release.error.message}</Alert>}
      {series.series_id !== null && <MemoryControls storedId={series.series_id} />}

      <RouteTabs base={`/series/${seriesId}`} tabs={TABS} keep={["lang"]} />

      <Outlet context={context} />
    </Stack>
  );
}
