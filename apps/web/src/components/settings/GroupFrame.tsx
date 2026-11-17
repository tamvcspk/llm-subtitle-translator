import { Accordion, Alert, Badge, Button, Group, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";

/** One group of Settings: its own Save, its own *Unsaved changes* (UX §6.2, wireframe 6); nothing here saves another group. */
export function GroupFrame({
  id,
  title,
  dirty,
  restored,
  saving,
  saved,
  error,
  blocked,
  onSave,
  onDiscard,
  children,
}: {
  id: string;
  title: string;
  dirty: boolean;
  /** The form was put back from an earlier visit (signing in again reloads the page). */
  restored: boolean;
  saving: boolean;
  saved: boolean;
  error: string | null;
  /** Why Save is off although something changed; `null` when it may go. */
  blocked: string | null;
  onSave: () => void;
  onDiscard: () => void;
  children: ReactNode;
}) {
  return (
    <Accordion.Item value={id}>
      <Accordion.Control style={{ minHeight: 56 }}>
        <Group justify="space-between" wrap="nowrap" gap="sm">
          <Text fw={600}>{title}</Text>
          {dirty && (
            <Badge color="yellow" variant="light">
              Unsaved changes
            </Badge>
          )}
        </Group>
      </Accordion.Control>
      <Accordion.Panel>
        <Stack gap="md">
          {restored && dirty && <Text size="sm" c="dimmed">Your unsaved changes were kept while you signed in again. Typed keys are never kept.</Text>}
          {children}
          {blocked && dirty && (
            <Text size="sm" c="red" role="alert">
              {blocked}
            </Text>
          )}
          {error && <Alert color="red">Save failed: {error}</Alert>}
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Text role="status" size="sm" c="teal.7">
              {saved && !dirty ? "✓ Saved" : ""}
            </Text>
            <Group gap="xs">
              <Button variant="default" disabled={!dirty || saving} onClick={onDiscard}>
                Discard
              </Button>
              <Button disabled={!dirty || blocked !== null} loading={saving} onClick={onSave} aria-label={`Save ${title}`}>
                Save
              </Button>
            </Group>
          </Group>
        </Stack>
      </Accordion.Panel>
    </Accordion.Item>
  );
}
