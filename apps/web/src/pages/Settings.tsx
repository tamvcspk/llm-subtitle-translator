import { Accordion, Alert, Loader, Stack, Title } from "@mantine/core";
import { useState } from "react";
import { useSettings, type SettingsAnswer } from "../api/queries";
import { ConnectionsGroup } from "../components/settings/ConnectionsGroup";
import { FieldGroup } from "../components/settings/FieldGroup";
import { LanguageLists, languagesProblem, MediaRoots, rootsProblem } from "../components/settings/ListParts";
import { MaintenanceTools } from "../components/settings/MaintenanceTools";
import { SecurityGroup } from "../components/settings/SecurityGroup";
import { keysOf, LIBRARY_FIELDS, MAINTENANCE_FIELDS, pick, QUEUE_FIELDS, STORY_FIELDS, TRANSLATION_FIELDS } from "../lib/settings";

const CONNECTION_KEYS = ["bazarr_url", "llm_provider", "llm_base_url"];
const LANGUAGE_KEYS = ["source_languages", "target_languages"];
const LIBRARY_EXTRA = ["media_roots"];

/** Settings (wireframe 6): seven groups, each saved by itself. The first two are open. */
function Groups({ settings }: { settings: SettingsAnswer }) {
  // A group starts again from what the worker has after a save: its key changes with the values it shows. So the page,
  // which lives on, remembers which group was saved last.
  const fresh = (keys: string[]) => JSON.stringify(pick(settings, keys));
  const [open, setOpen] = useState<string[]>(["connections", "translation"]);
  const [lastSaved, setLastSaved] = useState<string | null>(null);
  const saving = (id: string) => ({ settings, saved: lastSaved === id, onSaved: () => setLastSaved(id) });

  return (
    <Accordion multiple variant="separated" value={open} onChange={setOpen} chevronPosition="right">
      <ConnectionsGroup key={fresh(CONNECTION_KEYS)} {...saving("connections")} />
      <FieldGroup
        key={fresh([...keysOf(TRANSLATION_FIELDS), ...LANGUAGE_KEYS])}
        id="translation"
        title="2. Translation"
        fields={TRANSLATION_FIELDS}
        extraKeys={LANGUAGE_KEYS}
        extra={(props) => <LanguageLists {...props} />}
        extraProblem={languagesProblem}
        {...saving("translation")}
      />
      <FieldGroup
        key={fresh([...keysOf(LIBRARY_FIELDS), ...LIBRARY_EXTRA])}
        id="library"
        title="3. Library"
        fields={LIBRARY_FIELDS}
        extraKeys={LIBRARY_EXTRA}
        extra={(props) => <MediaRoots {...props} />}
        extraProblem={rootsProblem}
        {...saving("library")}
      />
      <FieldGroup key={fresh(keysOf(QUEUE_FIELDS))} id="queue" title="4. Queue and quota" fields={QUEUE_FIELDS} {...saving("queue")} />
      <FieldGroup key={fresh(keysOf(STORY_FIELDS))} id="story" title="5. Story and style" fields={STORY_FIELDS} {...saving("story")} />
      <FieldGroup
        key={fresh(keysOf(MAINTENANCE_FIELDS))}
        id="maintenance"
        title="6. Maintenance"
        fields={MAINTENANCE_FIELDS}
        footer={<MaintenanceTools />}
        {...saving("maintenance")}
      />
      <SecurityGroup />
    </Accordion>
  );
}

export function Settings() {
  const settings = useSettings();
  return (
    <Stack gap="md">
      <Title order={2}>Settings</Title>
      {settings.isPending && <Loader size="sm" aria-label="Loading settings" />}
      {settings.isError && <Alert color="red">{settings.error.message}</Alert>}
      {settings.data && <Groups settings={settings.data} />}
    </Stack>
  );
}
