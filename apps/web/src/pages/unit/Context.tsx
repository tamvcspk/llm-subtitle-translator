import { Alert, Badge, Card, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useOutletContext } from "react-router";
import { useUnitContext } from "../../api/queries";
import type { ContextSection } from "../../api/types";
import type { UnitContext } from "../Unit";

function Sections({ heading, sections, note }: { heading: string; sections: ContextSection[]; note?: string }) {
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label={heading}>
      <Stack gap="sm">
        <Title order={4}>{heading}</Title>
        {note && (
          <Text size="sm" c="dimmed">
            {note}
          </Text>
        )}
        {sections.length === 0 ? (
          <Text c="dimmed">Nothing yet.</Text>
        ) : (
          sections.map((section) => (
            <Stack key={section.title} gap={2}>
              <Text fw={600}>{section.title}</Text>
              <Text style={{ whiteSpace: "pre-wrap" }}>{section.body}</Text>
            </Stack>
          ))
        )}
      </Stack>
    </Card>
  );
}

/** Context tab (wireframe 3e): the story memory behind this translation, and what a new one would get. Read only. */
export function Context() {
  const { unit, target } = useOutletContext<UnitContext>();
  const context = useUnitContext(unit.id, target?.lang ?? "");

  if (!target) return <Alert color="yellow">There is no target language to show.</Alert>;
  if (context.isPending) return <Loader size="sm" aria-label="Loading context" />;
  if (context.isError) return <Alert color="red">{context.error.message}</Alert>;

  const { applicable, enabled, used, now } = context.data;
  if (!applicable) return <Alert color="blue">A movie has no earlier episodes, so it has no story memory.</Alert>;
  if (!enabled) {
    return <Alert color="blue">Story memory is off for this series. Turn it on under Story memory in the series header to give later episodes the story so far.</Alert>;
  }
  const missing = [...new Set([...(used?.missing ?? []), ...(now?.missing ?? [])])];

  return (
    <Stack gap="md">
      <Group justify="space-between" wrap="wrap">
        <Title order={3}>Episode context</Title>
        {used?.changed && (
          <Badge color="yellow" variant="light">
            Changed since this translation
          </Badge>
        )}
      </Group>
      {missing.length > 0 && <Alert color="yellow">Missing earlier episodes: {missing.join(", ")}</Alert>}
      {used ? (
        <Sections heading="Used for the current translation" sections={used.sections ?? []} />
      ) : (
        <Alert color="blue">The current translation was made without story memory.</Alert>
      )}
      {now && (
        <Sections
          heading="If translated now"
          sections={now.sections}
          note={now.approximate ? "Older summaries are only shortened here; a real translation would condense them with a model call." : undefined}
        />
      )}
    </Stack>
  );
}
