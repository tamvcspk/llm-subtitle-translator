import { Alert, Badge, Button, Collapse, Group, NumberInput, Select, SegmentedControl, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useState } from "react";
import { useSaveStyle, useStyleImpact, type StyleOwner } from "../api/queries";
import type { Preset, Style } from "../api/types";
import {
  AXES,
  INHERIT,
  NUMBER_FIELDS,
  axisValue,
  cleanDraft,
  impactSentence,
  inheritLabel,
  sameDraft,
  setAxis,
  type Axis,
  type StyleDraft,
} from "../lib/style";
import { ConfirmSheet } from "./ConfirmSheet";

export type StyleTarget = StyleOwner | { kind: "episode"; id: number };

export function AxisControl({ axis, style, effective, onChange }: { axis: Axis; style: Style; effective: Style; onChange: (style: Style) => void }) {
  const labelId = `axis-${axis.key}`;
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500} id={labelId}>
        {axis.label}
      </Text>
      <div style={{ overflowX: "auto" }}>
        <SegmentedControl
          aria-labelledby={labelId}
          value={axisValue(axis, style)}
          onChange={(value) => onChange(setAxis(style, axis, value))}
          data={[{ value: INHERIT, label: inheritLabel(axis, effective) }, ...axis.options]}
        />
      </div>
    </Stack>
  );
}

/**
 * The style form of a series, a movie or one episode (UX §6.1, §6.2): every axis starts at Inherit, the effect of saving
 * is counted live from a dry run, and Save asks once when it would flag anything stale. Nothing is retranslated.
 */
export function StyleForm({
  target,
  lang,
  stored,
  draft,
  onChange,
  effective,
  presets,
  suggestion,
  noun,
}: {
  target: StyleTarget;
  lang: string;
  stored: StyleDraft;
  draft: StyleDraft;
  onChange: (draft: StyleDraft) => void;
  effective: Style;
  /** `null` for an episode: its override has no preset. */
  presets: Preset[] | null;
  suggestion?: { genres: string[]; preset: { id: string; name: string } | null };
  noun: "episode" | "movie";
}) {
  const [more, setMore] = useState(false);
  const [asking, setAsking] = useState(false);
  const save = useSaveStyle(target, lang);
  const dirty = !sameDraft(draft, stored);
  const [settled] = useDebouncedValue(cleanDraft(draft), 400);
  const waiting = dirty && !sameDraft(settled, draft);
  const impact = useStyleImpact(target, lang, settled, dirty && !waiting);
  const sentence = impactSentence(impact.data, noun);
  const affected = impact.data?.would_stale ?? 0;

  const patch = (style: Style) => onChange({ ...draft, style });
  const set = <K extends keyof Style>(key: K, value: Style[K] | undefined) => {
    const next = { ...draft.style };
    if (value === undefined || value === "") delete next[key];
    else next[key] = value;
    patch(next);
  };
  const doSave = () => save.mutate(cleanDraft(draft), { onSuccess: () => setAsking(false) });
  const main = AXES.filter((axis) => !axis.more);
  const rest = AXES.filter((axis) => axis.more);

  return (
    <Stack gap="md">
      {presets && (
        <Stack gap="xs">
          <Select
            label="Preset"
            placeholder="None"
            clearable
            data={presets.map((preset) => ({ value: preset.id, label: preset.name }))}
            value={draft.preset || null}
            onChange={(value) => onChange({ ...draft, preset: value ?? "" })}
            style={{ maxWidth: 320 }}
          />
          {suggestion?.preset && draft.preset !== suggestion.preset.id && (
            <Group gap="xs">
              <Text size="sm" c="dimmed">
                Suggested for {suggestion.genres.join(", ")}:
              </Text>
              <Button variant="light" size="sm" onClick={() => onChange({ ...draft, preset: suggestion.preset!.id })}>
                Apply "{suggestion.preset.name}"
              </Button>
            </Group>
          )}
        </Stack>
      )}

      {main.slice(0, 1).map((axis) => (
        <AxisControl key={axis.key} axis={axis} style={draft.style} effective={effective} onChange={patch} />
      ))}
      <TextInput
        label="Tone"
        placeholder={effective.tone ? `Inherit · now ${effective.tone}` : "Inherit"}
        value={draft.style.tone ?? ""}
        onChange={(event) => set("tone", event.currentTarget.value)}
        rightSectionWidth={90}
        rightSection={
          draft.style.tone ? (
            <Button variant="subtle" size="compact-sm" onClick={() => set("tone", undefined)}>
              Inherit
            </Button>
          ) : null
        }
      />
      {main.slice(1).map((axis) => (
        <AxisControl key={axis.key} axis={axis} style={draft.style} effective={effective} onChange={patch} />
      ))}

      <Button variant="subtle" onClick={() => setMore((open) => !open)} style={{ alignSelf: "flex-start" }} aria-expanded={more}>
        {more ? "Fewer options" : "More"}
      </Button>
      <Collapse expanded={more}>
        <Stack gap="md">
          {rest.map((axis) => (
            <AxisControl key={axis.key} axis={axis} style={draft.style} effective={effective} onChange={patch} />
          ))}
          <Group align="flex-end" wrap="wrap">
            {NUMBER_FIELDS.map((field) => (
              <NumberInput
                key={field.key}
                label={field.label}
                placeholder={effective[field.key] !== undefined ? `Inherit · now ${effective[field.key]}` : "Inherit"}
                allowDecimal={false}
                allowNegative={false}
                min={1}
                hideControls
                value={draft.style[field.key] ?? ""}
                onChange={(value) => set(field.key, typeof value === "number" ? value : undefined)}
                style={{ flex: "1 1 10rem" }}
              />
            ))}
          </Group>
          <Textarea
            label="Notes"
            placeholder={effective.notes ? `Inherit · now ${effective.notes}` : "Inherit"}
            autosize
            minRows={2}
            value={draft.style.notes ?? ""}
            onChange={(event) => set("notes", event.currentTarget.value)}
          />
        </Stack>
      </Collapse>

      <Stack gap="xs">
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Stack gap={2}>
            {dirty && (
              <Badge color="yellow" variant="light" style={{ alignSelf: "flex-start" }}>
                Unsaved changes
              </Badge>
            )}
            <Text role="status" size="sm" c={impact.isError ? "red" : "dimmed"}>
              {!dirty
                ? "No changes."
                : waiting || impact.isFetching
                  ? "Checking…"
                  : impact.isError
                    ? `The effect could not be counted: ${impact.error.message}`
                    : sentence}
            </Text>
          </Stack>
          <Group gap="xs">
            <Button variant="default" disabled={!dirty} onClick={() => onChange(stored)}>
              Discard
            </Button>
            <Button
              disabled={!dirty || waiting || impact.isFetching}
              loading={save.isPending && !asking}
              onClick={() => (affected > 0 || impact.isError ? setAsking(true) : doSave())}
            >
              Save
            </Button>
          </Group>
        </Group>
        {save.isError && !asking && <Alert color="red">{save.error.message}</Alert>}
        {save.isSuccess && !dirty && (
          <Text size="sm" c="teal.7">
            Saved. Nothing was retranslated.
          </Text>
        )}
      </Stack>

      <ConfirmSheet
        opened={asking}
        title="Save style?"
        confirm="Confirm"
        pending={save.isPending}
        error={save.isError ? save.error.message : null}
        onClose={() => {
          setAsking(false);
          save.reset();
        }}
        onConfirm={doSave}
      >
        {impact.isError
          ? "The effect could not be counted. Saving may flag translations stale. Nothing is retranslated."
          : `${sentence} Nothing is retranslated.`}
      </ConfirmSheet>
    </Stack>
  );
}
