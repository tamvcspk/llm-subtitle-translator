import { Group, Text } from "@mantine/core";
import { useCatalogStatus, useStatus } from "../api/queries";
import { deriveStrip } from "../lib/strip";

/** Running and waiting jobs, catalog age and a provider that is out; small, never an alarm. */
export function StatusStrip() {
  const status = useStatus();
  const catalog = useCatalogStatus();
  const items = deriveStrip(status.data, catalog.data);
  if (items.length === 0) return null;
  return (
    <Group gap="md" wrap="wrap" aria-label="Status">
      {items.map((item) => (
        <Text key={item.id} size="sm" c={item.notable ? "orange" : "dimmed"}>
          {item.text}
        </Text>
      ))}
    </Group>
  );
}
