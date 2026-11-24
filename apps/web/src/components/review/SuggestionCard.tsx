import { Anchor, Badge, Button, Card, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router";
import { useSuggestionImpact } from "../../api/queries";
import type { Suggestion } from "../../api/types";
import { ago } from "../../lib/format";
import { describe, evidenceLines, impactLine, KIND_LABEL, safeUrl, SOURCE_LABEL, sourceName } from "../../lib/suggestions";
import { SearchSuggestions } from "./SearchSuggestions";

function Evidence({ suggestion }: { suggestion: Suggestion }) {
  const lines = evidenceLines(suggestion.evidence);
  const links = suggestion.sources.map((url) => ({ url, href: safeUrl(url) }));
  if (lines.length === 0 && links.length === 0) return <Text size="sm" c="dimmed">The generator gave no evidence.</Text>;
  const shown = lines.slice(0, 2);
  const rest = lines.slice(2);
  const line = (item: (typeof lines)[number], key: number) => (
    <Text size="sm" key={key} component="li" style={{ overflowWrap: "anywhere" }}>
      {item.where && (
        <Text span c="dimmed">
          {item.unitId !== undefined ? (
            <Anchor component={Link} to={`/unit/${item.unitId}/subtitles`} size="sm">
              {item.where}
            </Anchor>
          ) : (
            item.where
          )}
          {" · "}
        </Text>
      )}
      {item.text}
    </Text>
  );
  return (
    <Stack gap={4}>
      <Text size="xs" c="dimmed" fw={600}>
        Evidence
      </Text>
      <Stack gap={2} component="ul" aria-label="Evidence" style={{ margin: 0, paddingLeft: 18 }}>
        {shown.map(line)}
      </Stack>
      {rest.length > 0 && (
        <details>
          <summary>Show {rest.length} more</summary>
          <Stack gap={2} component="ul" style={{ margin: 0, paddingLeft: 18 }}>
            {rest.map((item, i) => line(item, i + shown.length))}
          </Stack>
        </details>
      )}
      {links.length > 0 && (
        <Stack gap={0} component="ul" aria-label="Sources" style={{ margin: 0, padding: 0, listStyle: "none" }}>
          <Text size="xs" c="dimmed" fw={600}>
            {links.length === 1 ? "1 source" : `${links.length.toLocaleString()} sources`}
          </Text>
          {links.map(({ url, href }) => (
            <li key={url} style={{ minHeight: 44, display: "flex", alignItems: "center" }}>
              {href ? (
                <Anchor href={href} target="_blank" rel="noreferrer" size="sm" style={{ overflowWrap: "anywhere" }}>
                  {sourceName(url)}
                </Anchor>
              ) : (
                <Text size="sm" style={{ overflowWrap: "anywhere" }}>
                  {url}
                </Text>
              )}
            </li>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

/** One thing the machine proposes: what it says, why, what accepting would flag stale, and the three decisions. */
export function PendingCard({
  suggestion,
  busy,
  onAccept,
  onEdit,
  onReject,
}: {
  suggestion: Suggestion;
  busy: boolean;
  onAccept: (suggestion: Suggestion) => void;
  onEdit: (suggestion: Suggestion) => void;
  onReject: (suggestion: Suggestion) => void;
}) {
  const { title, details } = describe(suggestion);
  const impact = useSuggestionImpact(suggestion.id, null, true);
  const line = impact.isError ? impact.error.message : impactLine(impact.data?.would_stale);
  return (
    <Card withBorder padding="md" radius="md" component="li" style={{ listStyle: "none" }} aria-label={`${KIND_LABEL[suggestion.kind]}: ${title}`}>
      <Stack gap="xs">
        <Group gap="xs" wrap="wrap">
          <Badge variant="light">{KIND_LABEL[suggestion.kind]}</Badge>
          <Badge variant="outline" color="gray">
            {SOURCE_LABEL[suggestion.source]}
          </Badge>
          <Badge variant="outline" color="gray">
            {suggestion.lang}
          </Badge>
        </Group>
        <Text fw={600} style={{ overflowWrap: "anywhere" }}>
          {title}
        </Text>
        {details.map((detail) => (
          <Text size="sm" c="dimmed" key={detail}>
            {detail}
          </Text>
        ))}
        <Evidence suggestion={suggestion} />
        <SearchSuggestions id={suggestion.search_display_id} />
        <Text size="sm" c={impact.isError ? "red" : "dimmed"} role="status">
          {line ?? "Counting…"}
        </Text>
        <Group gap="xs" wrap="wrap">
          <Button loading={busy} onClick={() => onAccept(suggestion)} aria-label={`Accept ${title}`}>
            Accept
          </Button>
          <Button variant="light" disabled={busy} onClick={() => onEdit(suggestion)} aria-label={`Edit ${title}`}>
            Edit
          </Button>
          <Button variant="default" color="red" disabled={busy} onClick={() => onReject(suggestion)} aria-label={`Reject ${title}`}>
            Reject
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

/** A decided suggestion: accepted (by hand or by auto-accept, with Undo) or rejected. */
export function DecidedCard({ suggestion, busy, onUndo }: { suggestion: Suggestion; busy: boolean; onUndo: (suggestion: Suggestion) => void }) {
  const { title, details } = describe(suggestion);
  const accepted = suggestion.status === "accepted";
  return (
    <Card withBorder padding="md" radius="md" component="li" style={{ listStyle: "none" }} aria-label={`${KIND_LABEL[suggestion.kind]}: ${title}`}>
      <Stack gap="xs">
        <Group gap="xs" wrap="wrap">
          <Badge color={accepted ? "teal" : "gray"}>{accepted ? "Accepted" : "Rejected"}</Badge>
          {suggestion.auto_accepted && <Badge variant="outline">Auto</Badge>}
          {suggestion.edited && <Badge variant="outline">Edited</Badge>}
          <Badge variant="light">{KIND_LABEL[suggestion.kind]}</Badge>
          <Badge variant="outline" color="gray">
            {SOURCE_LABEL[suggestion.source]}
          </Badge>
        </Group>
        <Text fw={600} style={{ overflowWrap: "anywhere" }}>
          {title}
        </Text>
        {details.map((detail) => (
          <Text size="sm" c="dimmed" key={detail}>
            {detail}
          </Text>
        ))}
        <SearchSuggestions id={suggestion.search_display_id} />
        <Text size="sm" c="dimmed">
          {suggestion.series} · {suggestion.lang} · {ago(suggestion.decided_at)}
          {!accepted && " · not proposed again"}
        </Text>
        {suggestion.undoable && (
          <Group>
            <Button variant="default" loading={busy} onClick={() => onUndo(suggestion)} aria-label={`Undo ${title}`}>
              Undo
            </Button>
          </Group>
        )}
      </Stack>
    </Card>
  );
}
