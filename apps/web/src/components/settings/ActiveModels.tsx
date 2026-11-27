import { Alert, Box, Group, Stack, Text } from "@mantine/core";
import { activeModels, modelWarnings, type Values } from "../../lib/settings";

/**
 * Under the model boxes of group 2: the warnings a change of them calls for, and the table of what each task runs on
 * (FR-9.3.1). It follows the draft, so what is typed is what the table says, before Save.
 */
export function ActiveModels({ draft, stored }: { draft: Values; stored: Values }) {
  const warnings = modelWarnings(stored, draft);
  return (
    <Stack gap="sm">
      {warnings.map((message) => (
        <Alert key={message} color="yellow" role="status">
          {message}
        </Alert>
      ))}
      <Box component="section" aria-labelledby="active-models-title">
        <Text size="sm" fw={500} id="active-models-title" mb={4}>
          Active models by task
        </Text>
        <Stack gap={0} component="ul" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {activeModels(draft).map((row) => (
            <Group
              key={row.label}
              component="li"
              justify="space-between"
              wrap="wrap"
              gap="xs"
              py={6}
              style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
            >
              <Text size="sm">{row.label}</Text>
              <Text size="sm" c={row.model && !row.off ? undefined : "dimmed"}>
                {row.off ?? (row.model || "—")}
                {!row.off && row.note ? <Text span c="dimmed"> {row.note}</Text> : null}
              </Text>
            </Group>
          ))}
        </Stack>
      </Box>
    </Stack>
  );
}
