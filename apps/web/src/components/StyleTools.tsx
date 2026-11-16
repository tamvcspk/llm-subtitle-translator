import { Alert, Button, Card, Group, NumberInput, Select, Stack, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";
import { usePreviewStyle, type StyleOwner } from "../api/queries";
import { cleanDraft, type StyleDraft } from "../lib/style";
import { EstimateSheet, type EstimateRequest } from "./EstimateSheet";

export interface SampleEpisode {
  unit_id: number;
  label: string;
}

const LINES = 20;

/** Try 20 lines (UX §6.1 steps 2 and 3): one model call on the draft, written nowhere; the cost is on the button. */
export function TryStyle({
  owner,
  lang,
  draft,
  episodes,
}: {
  owner: StyleOwner;
  lang: string;
  draft: StyleDraft;
  /** The episodes with a source subtitle; empty for a movie, whose own subtitle is the sample. */
  episodes: SampleEpisode[];
}) {
  const preview = usePreviewStyle(owner);
  const [unit, setUnit] = useState<string | null>(null);
  const [start, setStart] = useState<number | "">("");
  const rows = preview.data?.rows ?? [];
  const hasCurrent = rows.some((row) => row.current !== null);

  const run = () => {
    const clean = cleanDraft(draft);
    preview.mutate({
      lang,
      ...(unit ? { unit_id: Number(unit) } : {}),
      ...(typeof start === "number" ? { cues: Array.from({ length: LINES }, (_, i) => start + i) } : {}),
      preset: clean.preset,
      style: clean.style,
    });
  };

  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label="Try 20 lines">
      <Stack gap="sm">
        <Title order={4}>Try 20 lines</Title>
        <Text size="sm" c="dimmed">
          Translated now with the style in the form. Nothing is written.
        </Text>
        <Group align="flex-end" wrap="wrap">
          {episodes.length > 0 && (
            <Select
              label="Sample episode"
              placeholder="First translated episode"
              clearable
              data={episodes.map((episode) => ({ value: String(episode.unit_id), label: episode.label }))}
              value={unit}
              onChange={setUnit}
              style={{ flex: "1 1 14rem" }}
            />
          )}
          <NumberInput
            label="Start at cue"
            placeholder="Spread over the episode"
            allowDecimal={false}
            min={1}
            hideControls
            value={start}
            onChange={(value) => setStart(typeof value === "number" ? value : "")}
            style={{ flex: "1 1 10rem" }}
          />
          <Button loading={preview.isPending} onClick={run}>
            {preview.isPending ? "Trying…" : "Try 20 lines (≈ 1 call)"}
          </Button>
        </Group>
        {preview.isError && <Alert color="red">Preview failed: {preview.error.message}</Alert>}
        {preview.data && (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              {preview.data.model} · {preview.data.tokens_in.toLocaleString()} in · {preview.data.tokens_out.toLocaleString()} out
            </Text>
            <Stack component="ul" gap="xs" m={0} p={0} aria-label="Preview">
              {rows.map((row) => (
                <Card key={row.n} withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
                  <Stack gap={2}>
                    <Text size="sm" c="dimmed">
                      Cue #{row.n}
                    </Text>
                    <Text>
                      <Text span c="dimmed">
                        Source{" "}
                      </Text>
                      {row.source}
                    </Text>
                    {hasCurrent && (
                      <Text>
                        <Text span c="dimmed">
                          Now{" "}
                        </Text>
                        {row.current ?? "—"}
                      </Text>
                    )}
                    <Text fw={500}>
                      <Text span c="dimmed" fw={400}>
                        {hasCurrent ? "With this style " : "Preview "}
                      </Text>
                      {row.preview}
                    </Text>
                  </Stack>
                </Card>
              ))}
            </Stack>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

/** Compare on a whole episode (UX §6.1 step 5): a variant job, through Estimate, ending as a Candidate in History. */
export function CompareVariant({
  lang,
  draft,
  episodes,
}: {
  lang: string;
  draft: StyleDraft;
  episodes: SampleEpisode[];
}) {
  const [unit, setUnit] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [estimating, setEstimating] = useState<EstimateRequest | null>(null);
  const chosen = episodes.find((episode) => String(episode.unit_id) === (unit ?? String(episodes[0]?.unit_id)));

  const ask = () => {
    if (!chosen) return;
    const clean = cleanDraft(draft);
    setEstimating({
      action: `Compare on a whole episode · ${chosen.label}`,
      variant: { unit_id: chosen.unit_id, body: { lang, label: label.trim() || "variant", preset: clean.preset, style: clean.style } },
    });
  };

  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label="Compare on a whole episode">
      <Stack gap="sm">
        <Title order={4}>Compare on a whole episode</Title>
        <Text size="sm" c="dimmed">
          The whole episode is translated in the background with the style in the form, and kept as a Candidate in its History.
        </Text>
        <Group align="flex-end" wrap="wrap">
          <Select
            label="Episode"
            data={episodes.map((episode) => ({ value: String(episode.unit_id), label: episode.label }))}
            value={chosen ? String(chosen.unit_id) : null}
            onChange={setUnit}
            allowDeselect={false}
            disabled={episodes.length === 0}
            style={{ flex: "1 1 14rem" }}
          />
          <TextInput label="Label" placeholder="gritty" value={label} onChange={(event) => setLabel(event.currentTarget.value)} style={{ flex: "1 1 10rem" }} />
          <Button variant="light" disabled={!chosen} onClick={ask}>
            Compare on a whole episode
          </Button>
        </Group>
        {episodes.length === 0 && <Text size="sm" c="dimmed">No episode has a source subtitle to translate.</Text>}
      </Stack>
      <EstimateSheet request={estimating} onClose={() => setEstimating(null)} />
    </Card>
  );
}
