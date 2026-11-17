import { Button, Group, PasswordInput, SegmentedControl, Stack, Text, TextInput } from "@mantine/core";
import { useCallback, useMemo, useState } from "react";
import { useSaveSettings, useTestConnection, type SettingsAnswer } from "../../api/queries";
import { useDraft } from "../../lib/draft";
import { KEY_SLOTS, pick, type Values } from "../../lib/settings";
import { GroupFrame } from "./GroupFrame";

const KEYS = ["bazarr_url", "llm_provider", "llm_base_url"];
const text = (value: unknown) => (typeof value === "string" ? value : "");

/** `••••9a1f · ok`, or `(not set)`: what a stored key may say about itself (never its value). */
function keyState(settings: SettingsAnswer, field: string): string {
  if (settings[`${field}_configured`] !== true) return "(not set)";
  const last4 = text(settings[`${field}_last4`]);
  const status = text(settings[`${field}_status`]);
  return [last4 ? `••••${last4}` : "set", status].filter(Boolean).join(" · ");
}

function KeyRow({
  slot,
  settings,
  bazarrUrl,
  typed,
  onType,
}: {
  slot: (typeof KEY_SLOTS)[number];
  settings: SettingsAnswer;
  bazarrUrl: string;
  typed: string;
  onType: (value: string) => void;
}) {
  const test = useTestConnection();
  const result = test.data;
  return (
    <Stack gap={4}>
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Text fw={500}>{slot.label}</Text>
        <Text size="sm" c={text(settings[`${slot.field}_status`]) === "invalid" ? "red" : "dimmed"}>
          {keyState(settings, slot.field)}
        </Text>
      </Group>
      <Group align="flex-end" wrap="nowrap" gap="xs">
        <PasswordInput
          aria-label={`New key for ${slot.label}`}
          placeholder="New key"
          autoComplete="off"
          value={typed}
          onChange={(event) => onType(event.currentTarget.value)}
          style={{ flex: 1 }}
        />
        <Button
          variant="default"
          loading={test.isPending}
          aria-label={`Test ${slot.label}`}
          onClick={() => test.mutate({ kind: slot.kind, bazarr_url: bazarrUrl, ...(typed.trim() ? { [slot.field]: typed.trim() } : {}) })}
        >
          Test
        </Button>
      </Group>
      {test.isError && (
        <Text size="sm" c="red" role="status">
          ✗ {test.error.message}
        </Text>
      )}
      {result && (
        <Text size="sm" c={result.ok ? "teal.7" : "red"} role="status">
          {result.ok ? `✓ ${result.message || "Key valid"}` : result.auth_failed ? `✗ Rejected: ${result.message}` : `✗ ${result.message}`}
        </Text>
      )}
    </Stack>
  );
}

/**
 * Connections (wireframe 6.1): Bazarr's address, the five keys, and which provider the models run on. A key is typed in a
 * box and saved with the group, or tried first with Test; pasting a new key, or a Test that works, makes it `ok` again.
 * A typed key lives only in this box: it is never kept as a draft.
 */
export function ConnectionsGroup({ settings, saved, onSaved }: { settings: SettingsAnswer; saved: boolean; onSaved: () => void }) {
  const stored = useMemo(() => pick(settings, KEYS), [settings]);
  const same = useCallback((a: Values, b: Values) => KEYS.every((key) => text(a[key]) === text(b[key])), []);
  const { draft, set, reset, restored } = useDraft<Values>("settings:connections", stored, same);
  const [typed, setTyped] = useState<Record<string, string>>({});
  const save = useSaveSettings(onSaved);

  const anyTyped = Object.values(typed).some((value) => value.trim() !== "");
  const dirty = !same(draft, stored) || anyTyped;
  const provider = text(draft.llm_provider) === "openai_compat" ? "openai_compat" : "gemini";
  const blocked = provider === "openai_compat" && text(draft.llm_base_url).trim() === "" ? "Give the address of the OpenAI-compatible server." : null;

  const onSave = () => {
    const body: Values = { bazarr_url: text(draft.bazarr_url).trim(), llm_provider: provider, llm_base_url: text(draft.llm_base_url).trim() };
    for (const [field, value] of Object.entries(typed)) if (value.trim()) body[field] = value.trim();
    save.mutate(body, { onSuccess: () => setTyped({}) }); // what was typed is stored now; the box starts empty again
  };

  return (
    <GroupFrame
      id="connections"
      title="1. Connections"
      dirty={dirty}
      restored={restored}
      saving={save.isPending}
      saved={saved}
      error={save.isError ? save.error.message : null}
      blocked={blocked}
      onSave={onSave}
      onDiscard={() => {
        save.reset();
        setTyped({});
        reset();
      }}
    >
      <Stack gap="md">
        <TextInput
          label="Bazarr URL"
          placeholder="http://bazarr:6767"
          value={text(draft.bazarr_url)}
          onChange={(event) => set({ bazarr_url: event.currentTarget.value })}
        />
        <Stack gap="md">
          <Text fw={600}>API keys</Text>
          {KEY_SLOTS.map((slot) => (
            <KeyRow
              key={slot.field}
              slot={slot}
              settings={settings}
              bazarrUrl={text(draft.bazarr_url)}
              typed={typed[slot.field] ?? ""}
              onType={(value) => setTyped({ ...typed, [slot.field]: value })}
            />
          ))}
          <Text size="xs" c="dimmed">
            Pasting a new key, or a Test that succeeds, sets the key back to ok.
          </Text>
        </Stack>
        <Stack gap={4}>
          <Text size="sm" fw={500} id="llm-provider-label">
            LLM provider
          </Text>
          <SegmentedControl
            aria-labelledby="llm-provider-label"
            data={[
              { value: "gemini", label: "Gemini" },
              { value: "openai_compat", label: "OpenAI-compatible" },
            ]}
            value={provider}
            onChange={(value) => set({ llm_provider: value })}
          />
        </Stack>
        {provider === "openai_compat" && (
          <TextInput
            label="Base URL"
            description="The server's /v1 address, for example http://ollama:11434/v1"
            value={text(draft.llm_base_url)}
            onChange={(event) => set({ llm_base_url: event.currentTarget.value })}
          />
        )}
      </Stack>
    </GroupFrame>
  );
}
