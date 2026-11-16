import { Alert, Anchor, Card, Group, SegmentedControl, Select, Skeleton, Stack, Tabs, Text, Title } from "@mantine/core";
import { IconChevronLeft } from "@tabler/icons-react";
import { Link, Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { useSeriesPage, useSetAutoRequest } from "../api/queries";
import type { SeriesPage } from "../api/types";
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
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const tab = TABS.find((candidate) => location.pathname.split("/")[3] === candidate.value)?.value ?? "episodes";

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

  const goTo = (value: string | null) => {
    if (!value) return;
    void navigate({ pathname: `/series/${seriesId}/${value}`, search: lang && params.has("lang") ? `?lang=${lang}` : "" });
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

      <Tabs value={tab} onChange={goTo} keepMounted={false}>
        <Tabs.List style={{ overflowX: "auto", flexWrap: "nowrap" }}>
          {TABS.map((item) => (
            <Tabs.Tab key={item.value} value={item.value} style={{ minHeight: 44 }}>
              {item.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>

      <Outlet context={context} />
    </Stack>
  );
}
