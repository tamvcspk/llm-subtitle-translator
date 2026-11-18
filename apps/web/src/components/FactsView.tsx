import { Alert, Badge, Group, Stack, Text } from "@mantine/core";
import type { UnitFacts } from "../api/types";

const HIERARCHY: Record<string, string> = { a_above: "first is senior", b_above: "second is senior", equal: "equals" };

function Heading({ children, count }: { children: string; count: number }) {
  return (
    <Text fw={600} size="sm">
      {children} <Text span c="dimmed">({count})</Text>
    </Text>
  );
}

/** What the stored facts of one episode say (read only): the story so far is built from these. Quotes are the model's, copied from the source subtitle. */
export function FactsView({ facts }: { facts: UnitFacts }) {
  if (!facts.stored || facts.facts === null) {
    return <Alert color="blue">The facts of {facts.label} have not been read yet.</Alert>;
  }
  const { summary, characters, relationship_events: events, terms } = facts.facts;
  return (
    <Stack gap="sm" aria-label={`Facts of ${facts.label}`} pb="sm">
      {summary && (
        <Stack gap={2}>
          <Text fw={600} size="sm">
            Summary
          </Text>
          <Text size="sm">{summary}</Text>
        </Stack>
      )}

      <Stack gap={4}>
        <Heading count={characters.length}>Characters</Heading>
        {characters.length === 0 && (
          <Text size="sm" c="dimmed">
            None named.
          </Text>
        )}
        {characters.map((character) => {
          const about = [character.gender && `gender ${character.gender}`, character.age_hint, character.role].filter(Boolean).join(", ");
          return (
            <Text key={character.name} size="sm">
              <b>{character.name}</b>
              {about && ` · ${about}`}
              {character.aliases.length > 0 && ` · also ${character.aliases.join(", ")}`}
            </Text>
          );
        })}
      </Stack>

      <Stack gap={4}>
        <Heading count={events.length}>Relationships</Heading>
        {events.length === 0 && (
          <Text size="sm" c="dimmed">
            None shown by the dialogue.
          </Text>
        )}
        {events.map((event, index) => (
          <Stack key={`${event.a}-${event.b}-${event.cue}-${index}`} gap={2}>
            <Group gap="xs" wrap="wrap">
              <Text size="sm" fw={500}>
                {event.a} – {event.b}
              </Text>
              <Badge variant="light" size="sm">
                {event.relation}
              </Badge>
              {event.detail && (
                <Text size="sm" c="dimmed">
                  {event.detail}
                </Text>
              )}
            </Group>
            <Text size="xs" c="dimmed">
              {HIERARCHY[event.hierarchy] ?? event.hierarchy} · closeness {event.intimacy}/5 · confidence {Math.round(event.confidence * 100)}% · cue {event.cue}
              {event.evidence && ` · “${event.evidence}”`}
            </Text>
          </Stack>
        ))}
      </Stack>

      <Stack gap={4}>
        <Heading count={terms.length}>Terms</Heading>
        {terms.length === 0 ? (
          <Text size="sm" c="dimmed">
            None.
          </Text>
        ) : (
          <Group gap="xs" wrap="wrap">
            {terms.map((term) => (
              <Badge key={term.source} variant="outline" size="md" tt="none">
                {term.kind ? `${term.source} · ${term.kind}` : term.source}
              </Badge>
            ))}
          </Group>
        )}
      </Stack>
    </Stack>
  );
}
