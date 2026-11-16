import { Alert, Button, Drawer, Group, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";

/** A bottom sheet that asks once before something that replaces a file. It spends no model calls (those use EstimateSheet). */
export function ConfirmSheet({
  opened,
  title,
  confirm,
  pending,
  error,
  onConfirm,
  onClose,
  children,
}: {
  opened: boolean;
  title: string;
  confirm: string;
  pending: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Drawer opened={opened} onClose={onClose} position="bottom" size="auto" title={title} padding="lg" radius="lg">
      <Stack>
        <Text>{children}</Text>
        {error && <Alert color="red">{error}</Alert>}
        <Group justify="space-between">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={pending} onClick={onConfirm}>
            {confirm}
          </Button>
        </Group>
      </Stack>
    </Drawer>
  );
}
