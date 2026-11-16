import { Alert, Button, Card, Group, Loader, Stack, Text } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { useState } from "react";
import { useOutletContext } from "react-router";
import { useEpisodeStyle, useOwnerStyle } from "../../api/queries";
import { OwnerStyle } from "../../components/OwnerStyle";
import { StyleForm } from "../../components/StyleForm";
import { cleanStyle, type StyleDraft } from "../../lib/style";
import type { UnitContext } from "../Unit";

function Override({ unitId, seriesId, lang }: { unitId: number; seriesId: number; lang: string }) {
  const own = useEpisodeStyle(unitId);
  // "now" under each axis is the series' own effective style: what the episode inherits unless it overrides.
  const series = useOwnerStyle({ kind: "series", id: seriesId }, lang);
  if (own.isPending || series.isPending) return <Loader size="sm" aria-label="Loading style" />;
  if (own.isError) return <Alert color="red">{own.error.message}</Alert>;
  const stored: StyleDraft = { preset: "", style: cleanStyle(own.data.style) };
  return <OverrideEditor key={JSON.stringify(stored.style)} unitId={unitId} lang={lang} stored={stored} effective={series.data?.effective ?? {}} />;
}

function OverrideEditor({ unitId, lang, stored, effective }: { unitId: number; lang: string; stored: StyleDraft; effective: StyleDraft["style"] }) {
  const [draft, setDraft] = useState<StyleDraft>(stored);
  const hasOverride = Object.keys(stored.style).length > 0;
  return (
    <Stack gap="md">
      <Card withBorder padding="md" radius="md">
        <Group justify="space-between" wrap="wrap" gap="sm" align="center">
          <Group gap="xs" wrap="nowrap">
            <IconInfoCircle size={20} aria-hidden />
            <Text>Overrides the series style for this episode only.</Text>
          </Group>
          <Button variant="default" disabled={Object.keys(draft.style).length === 0} onClick={() => setDraft({ preset: "", style: {} })}>
            Clear episode override
          </Button>
        </Group>
        {!hasOverride && (
          <Text size="sm" c="dimmed" mt="xs">
            This episode follows the series style.
          </Text>
        )}
      </Card>
      <StyleForm
        target={{ kind: "episode", id: unitId }}
        lang={lang}
        stored={stored}
        draft={draft}
        onChange={(next) => setDraft({ preset: "", style: next.style })}
        effective={effective}
        presets={null}
        noun="episode"
      />
    </Stack>
  );
}

/** Style tab of one episode or movie (wireframe 3f): an episode overrides its series' style; a movie has a style of its own. */
export function UnitStyle() {
  const { unit, target } = useOutletContext<UnitContext>();
  const lang = target?.lang ?? "";
  if (!target) return <Alert color="yellow">There is no target language to set a style for.</Alert>;
  if (unit.series_id !== null) return <Override unitId={unit.id} seriesId={unit.series_id} lang={lang} />;
  return <OwnerStyle owner={{ kind: "movie", id: unit.id }} lang={lang} episodes={unit.source_exists ? [{ unit_id: unit.id, label: unit.title }] : []} />;
}
