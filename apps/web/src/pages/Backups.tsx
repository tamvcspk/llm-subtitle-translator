import { Alert, Anchor, Button, Card, Checkbox, Drawer, FileInput, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { IconChevronLeft } from "@tabler/icons-react";
import { useState } from "react";
import { Link } from "react-router";
import { useBackups, useCreateBackup, useImportBackup } from "../api/queries";
import { formatBytes } from "../lib/settings";

function ImportSheet({ file, onClose }: { file: File | null; onClose: () => void }) {
  const restore = useImportBackup();
  const close = () => {
    restore.reset();
    onClose();
  };
  const done = restore.data;
  return (
    <Drawer opened={file !== null} onClose={close} position="bottom" size="auto" title="Import backup?" padding="lg" radius="lg">
      <Stack>
        {done ? (
          <>
            <Text>Imported {done.imported.join(", ")}. The new settings are live.</Text>
            <Text size="sm" c="dimmed">
              The settings you had before were kept in {done.pre_import_backup.name}.
            </Text>
            <Button onClick={close}>Close</Button>
          </>
        ) : (
          <>
            <Text>
              {file?.name} replaces the saved settings, and its keys are added if it has any. The console account is never restored. The settings you
              have now are kept in a backup first.
            </Text>
            {restore.isError && <Alert color="red">{restore.error.message}</Alert>}
            <Group justify="space-between">
              <Button variant="default" onClick={close}>
                Cancel
              </Button>
              <Button loading={restore.isPending} onClick={() => file && restore.mutate(file)}>
                Import
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}

/** Backups (wireframe 7a): a backup holds settings only; keys are left out unless asked for. Download is a link the server serves. */
export function Backups() {
  const backups = useBackups();
  const create = useCreateBackup();
  const [withKeys, setWithKeys] = useState(false);
  const [chosen, setChosen] = useState<File | null>(null);
  const [picker, setPicker] = useState(0); // a new key empties the file box once the sheet is closed

  return (
    <Stack gap="md">
      <Stack gap="xs">
        <Anchor component={Link} to="/settings" size="sm" style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
          <IconChevronLeft size={16} /> Settings
        </Anchor>
        <Title order={2}>Backups</Title>
      </Stack>

      <Card withBorder padding="md" radius="md">
        <Stack gap="sm">
          <Text>A backup holds settings only. Keys are left out unless you tick below.</Text>
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Checkbox
              label="Include keys"
              description="Anyone who gets the file gets the keys."
              checked={withKeys}
              onChange={(event) => setWithKeys(event.currentTarget.checked)}
            />
            <Button loading={create.isPending} onClick={() => create.mutate(withKeys)}>
              Create backup
            </Button>
          </Group>
          {create.isError && <Alert color="red">{create.error.message}</Alert>}
          {create.isSuccess && (
            <Text size="sm" c="teal.7" role="status">
              ✓ Created {create.data.backup.name}
            </Text>
          )}
          <FileInput
            key={picker}
            label="Import backup…"
            placeholder="Choose a .zip"
            accept=".zip,application/zip"
            clearable
            value={null}
            onChange={setChosen}
          />
        </Stack>
      </Card>

      {backups.isPending && <Loader size="sm" aria-label="Loading backups" />}
      {backups.isError && <Alert color="red">{backups.error.message}</Alert>}
      {backups.data && backups.data.items.length === 0 && <Text c="dimmed">No backup yet.</Text>}
      {backups.data && backups.data.items.length > 0 && (
        <Stack gap="xs" component="ul" aria-label="Backups" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {backups.data.items.map((item) => (
            <Group component="li" key={item.name} justify="space-between" wrap="wrap" gap="sm" mih={44}>
              <Stack gap={0}>
                <Text>{item.name}</Text>
                <Text size="sm" c="dimmed">
                  {new Date(item.created_at * 1000).toLocaleString()} · {formatBytes(item.size)}
                </Text>
              </Stack>
              <Button component="a" variant="default" href={`/api/backups/download?name=${encodeURIComponent(item.name)}`} download={item.name} aria-label={`Download ${item.name}`}>
                Download
              </Button>
            </Group>
          ))}
        </Stack>
      )}

      <ImportSheet
        file={chosen}
        onClose={() => {
          setChosen(null);
          setPicker((n) => n + 1);
        }}
      />
    </Stack>
  );
}
