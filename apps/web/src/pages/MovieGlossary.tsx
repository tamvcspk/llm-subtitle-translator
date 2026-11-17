import { Alert, Anchor, Card, Select, Skeleton, Stack, Text, Title } from "@mantine/core";
import { IconChevronLeft } from "@tabler/icons-react";
import { Link, Navigate, useParams, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { useUnit } from "../api/queries";
import { OwnerGlossary } from "../components/OwnerGlossary";
import { languageName } from "../lib/home";

/** The glossary of one movie (UX §3, "Movie Glossary →"): its own Terms and Forms of address, never a series'. */
export function MovieGlossary() {
  const { id } = useParams();
  const unitId = Number(id);
  const unit = useUnit(unitId);
  const [params, setParams] = useSearchParams();

  if (unit.isPending) {
    return (
      <Stack gap="sm" aria-label="Loading">
        <Skeleton height={32} width="40%" />
        <Skeleton height={72} radius="md" />
      </Stack>
    );
  }
  if (unit.isError) {
    return (
      <Card withBorder padding="md">
        <Stack gap="xs">
          <Text>{unit.error instanceof ApiError && unit.error.status === 404 ? "That movie is not known to the worker." : "The movie could not be loaded."}</Text>
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

  const movie = unit.data;
  // An episode's glossary is its series': that page is the one to show.
  if (movie.series_id !== null) return <Navigate to={`/unit/${unitId}`} replace />;

  const asked = params.get("lang");
  const target = movie.targets.find((candidate) => candidate.lang === asked) ?? movie.targets[0] ?? null;
  const back = `/unit/${unitId}/subtitles${target ? `?lang=${encodeURIComponent(target.lang)}` : ""}`;

  return (
    <Stack gap="md">
      <Stack gap="xs">
        <Anchor component={Link} to={back} size="sm" style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
          <IconChevronLeft size={16} /> {movie.title}
        </Anchor>
        <Title order={2}>Glossary · {movie.title}</Title>
      </Stack>

      {movie.targets.length > 1 && (
        <Select
          label="Target language"
          data={movie.targets.map((item) => ({ value: item.lang, label: item.language || languageName(item.lang) }))}
          value={target?.lang ?? null}
          onChange={(value) => value && setParams({ lang: value }, { replace: true })}
          allowDeselect={false}
          style={{ maxWidth: 260 }}
        />
      )}

      {target ? (
        <OwnerGlossary key={target.lang} owner={{ kind: "movie", id: unitId }} lang={target.lang} />
      ) : (
        <Alert color="yellow">No target language is configured for this movie.</Alert>
      )}
    </Stack>
  );
}
