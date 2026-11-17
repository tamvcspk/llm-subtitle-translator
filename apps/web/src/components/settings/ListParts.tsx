import { ActionIcon, Button, Checkbox, Group, Select, Stack, Text, TextInput } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useLanguages } from "../../api/queries";
import type { LanguageEntry, Values } from "../../lib/settings";

const asEntries = (value: unknown): LanguageEntry[] =>
  Array.isArray(value)
    ? value
        .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
        .map((item) => ({ code: String(item.code ?? ""), language: String(item.language ?? item.name ?? ""), enabled: item.enabled !== false }))
    : [];

/** The languages of one list (`source_languages` or `target_languages`): a box to tick for each, and Add from Bazarr's list. */
function LanguageList({
  label,
  field,
  draft,
  set,
}: {
  label: string;
  field: "source_languages" | "target_languages";
  draft: Values;
  set: (patch: Values) => void;
}) {
  const entries = asEntries(draft[field]);
  const known = useLanguages();
  const have = new Set(entries.map((entry) => entry.code));
  const offer = (known.data?.items ?? []).filter((item) => !have.has(item.code));

  const change = (next: LanguageEntry[]) => set({ [field]: next });
  return (
    <Stack gap="xs" component="fieldset" style={{ border: 0, margin: 0, padding: 0 }}>
      <Text component="legend" size="sm" fw={500}>
        {label}
      </Text>
      <Group gap="md" wrap="wrap">
        {entries.map((entry, index) => (
          <Checkbox
            key={entry.code}
            label={`${entry.language} (${entry.code})`}
            checked={entry.enabled}
            onChange={(event) => change(entries.map((item, i) => (i === index ? { ...item, enabled: event.currentTarget.checked } : item)))}
            styles={{ root: { minHeight: 44, display: "flex", alignItems: "center" } }}
          />
        ))}
      </Group>
      {known.isError ? (
        <Text size="xs" c="dimmed">
          Bazarr's list of languages is not available now, so none can be added.
        </Text>
      ) : (
        <Select
          aria-label={`Add to ${label}`}
          placeholder="+ Add language"
          data={offer.map((item) => ({ value: item.code, label: `${item.language || item.name} (${item.code})` }))}
          value={null}
          searchable
          disabled={known.isPending || offer.length === 0}
          onChange={(code) => {
            const found = offer.find((item) => item.code === code);
            if (found) change([...entries, { code: found.code, language: found.language || found.name, enabled: true }]);
          }}
          style={{ maxWidth: 320 }}
        />
      )}
    </Stack>
  );
}

/** At least one language of each list must stay ticked: the worker puts its defaults back when none is. */
export function languagesProblem(draft: Values): string | null {
  const none = (field: string) => !asEntries(draft[field]).some((entry) => entry.enabled);
  if (none("source_languages")) return "Keep at least one source language ticked.";
  if (none("target_languages")) return "Keep at least one target language ticked.";
  return null;
}

export function LanguageLists({ draft, set }: { draft: Values; set: (patch: Values) => void }) {
  return (
    <Stack gap="md">
      <LanguageList label="Source languages" field="source_languages" draft={draft} set={set} />
      <LanguageList label="Target languages" field="target_languages" draft={draft} set={set} />
    </Stack>
  );
}

const asRoots = (value: unknown): string[] => (Array.isArray(value) ? value.map((item) => String(item)) : []);

/** Where the media lives (the same paths Bazarr sees). At least one stays. */
export function MediaRoots({ draft, set }: { draft: Values; set: (patch: Values) => void }) {
  const roots = asRoots(draft.media_roots);
  const change = (next: string[]) => set({ media_roots: next });
  return (
    <Stack gap="xs" component="fieldset" style={{ border: 0, margin: 0, padding: 0 }}>
      <Text component="legend" size="sm" fw={500}>
        Media roots
      </Text>
      {roots.map((root, index) => (
        <Group key={index} wrap="nowrap" gap="xs">
          <TextInput
            aria-label={`Media root ${index + 1}`}
            value={root}
            onChange={(event) => change(roots.map((item, i) => (i === index ? event.currentTarget.value : item)))}
            style={{ flex: 1 }}
          />
          <ActionIcon
            variant="default"
            size={44}
            aria-label={`Remove media root ${index + 1}`}
            disabled={roots.length <= 1}
            onClick={() => change(roots.filter((_, i) => i !== index))}
          >
            <IconTrash size={18} />
          </ActionIcon>
        </Group>
      ))}
      <Button variant="light" leftSection={<IconPlus size={16} />} onClick={() => change([...roots, ""])} style={{ alignSelf: "flex-start" }}>
        Add path
      </Button>
    </Stack>
  );
}

export function rootsProblem(draft: Values): string | null {
  const roots = asRoots(draft.media_roots).map((root) => root.trim());
  return roots.length === 0 || roots.every((root) => root === "") ? "Give at least one media folder." : null;
}
