import { Stack, Text, Title } from "@mantine/core";

/** A route that exists so the shell can be walked; the screen itself is a later item of PLAN P12b. */
export function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <Stack gap="xs">
      <Title order={2}>{title}</Title>
      <Text c="dimmed">{note}</Text>
    </Stack>
  );
}
