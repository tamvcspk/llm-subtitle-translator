import { SimpleGrid, Stack } from "@mantine/core";
import { useCallback, useMemo, type ReactNode } from "react";
import { useModels, useSaveSettings, type SettingsAnswer } from "../../api/queries";
import { useDraft } from "../../lib/draft";
import { fieldProblem, keysOf, pick, sameValue, toWire, type Field, type Values } from "../../lib/settings";
import { FieldInput } from "./FieldInput";
import { GroupFrame } from "./GroupFrame";

const NO_KEYS: string[] = [];

export interface ExtraProps {
  draft: Values;
  set: (patch: Values) => void;
}

/**
 * A group of Settings made of fields (UX Appendix B) and, when the group needs one, a part of its own (`extra`, with the
 * keys it edits). It keeps its draft, counts what changed, refuses what the worker would clamp, and saves only its own
 * keys. The page mounts it again (a `key`) after a save, so it starts from what the worker now has.
 */
export function FieldGroup({
  id,
  title,
  fields,
  extraKeys = NO_KEYS,
  extra,
  extraProblem,
  footer,
  settings,
  saved,
  onSaved,
}: {
  id: string;
  title: string;
  fields: Field[];
  extraKeys?: string[];
  extra?: (props: ExtraProps) => ReactNode;
  /** What is wrong with the part the group edits itself; `null` when nothing is. */
  extraProblem?: (draft: Values) => string | null;
  footer?: ReactNode;
  settings: SettingsAnswer;
  /** The page says whether this group was the last one saved (it starts again after a save, so it cannot remember). */
  saved: boolean;
  onSaved: () => void;
}) {
  const byKey = useMemo(() => new Map(fields.map((field) => [field.key, field])), [fields]);
  const keys = useMemo(() => [...keysOf(fields), ...extraKeys], [fields, extraKeys]);
  const stored = useMemo(() => pick(settings, keys), [settings, keys]);
  const same = useCallback(
    (a: Values, b: Values) => keys.every((key) => sameValue(byKey.get(key), a[key], b[key])),
    [keys, byKey],
  );
  const { draft, set, reset, restored } = useDraft<Values>(`settings:${id}`, stored, same);
  const save = useSaveSettings(onSaved);
  const models = useModels(String(settings.llm_provider ?? "gemini"), String(settings.llm_base_url ?? ""));

  const dirty = !same(draft, stored);
  // A field says what is wrong with it beside itself; the group only says that something is.
  const fieldWrong = fields.some((field) => fieldProblem(field, draft[field.key]) !== null);
  const problem = fieldWrong ? "Some fields need a different value." : (extraProblem?.(draft) ?? null);

  const onSave = () => {
    const body: Values = {};
    for (const key of keys) {
      const field = byKey.get(key);
      const value = draft[key];
      // A list of paths: a row left blank is not a path.
      const list = Array.isArray(value) && value.every((item) => typeof item === "string");
      body[key] = field ? toWire(field, value) : list ? (value as string[]).map((item) => item.trim()).filter(Boolean) : value;
    }
    save.mutate(body);
  };

  const fieldsPart = (
    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" verticalSpacing="md">
      {fields.map((field) => (
        <FieldInput
          key={field.key}
          field={field}
          value={draft[field.key]}
          models={models.data?.items ?? []}
          onChange={(value) => set({ [field.key]: value })}
        />
      ))}
    </SimpleGrid>
  );

  return (
    <GroupFrame
      id={id}
      title={title}
      dirty={dirty}
      restored={restored}
      saving={save.isPending}
      saved={saved}
      error={save.isError ? save.error.message : null}
      blocked={problem}
      onSave={onSave}
      onDiscard={() => {
        save.reset();
        reset();
      }}
    >
      <Stack gap="md">
        {fieldsPart}
        {extra?.({ draft, set })}
        {footer}
      </Stack>
    </GroupFrame>
  );
}
