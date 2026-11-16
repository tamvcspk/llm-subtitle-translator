import { Alert, Badge, Button, Card, Drawer, Group, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconLock, IconSearch } from "@tabler/icons-react";
import { useState } from "react";
import { useOutletContext } from "react-router";
import { useCorrections, useCues, useEditCue, useRemoveCorrection } from "../../api/queries";
import type { CueRow } from "../../api/types";
import { formatMs, isPlayerTime } from "../../lib/unit";
import type { UnitContext } from "../Unit";

const PAGE = 50;

function CueSheet({
  unitId,
  lang,
  rows,
  index,
  onMove,
  onClose,
  onWarnings,
}: {
  unitId: number;
  lang: string;
  rows: CueRow[];
  index: number;
  onMove: (index: number) => void;
  onClose: () => void;
  onWarnings: (warnings: string[]) => void;
}) {
  const cue = rows[index];
  const corrections = useCorrections(unitId, lang);
  const save = useEditCue(unitId);
  const unlock = useRemoveCorrection(unitId);
  const [draft, setDraft] = useState<{ n: number; text: string } | null>(null);
  if (!cue) return null;

  const text = draft && draft.n === cue.n ? draft.text : (cue.target ?? "");
  const lock = corrections.data?.items.find((item) => item.kind === "cue_edit" && Number(item.payload.cue) === cue.n);
  const move = (to: number) => {
    setDraft(null);
    save.reset();
    onMove(to);
  };

  return (
    <Stack>
      <Group justify="space-between">
        <Text fw={600}>
          {formatMs(cue.start_ms)} → {formatMs(cue.end_ms)}
        </Text>
        {cue.locked && (
          <Group gap="xs">
            <Badge leftSection={<IconLock size={12} />} variant="light">
              Locked
            </Badge>
            <Button
              variant="default"
              size="sm"
              disabled={!lock}
              loading={unlock.isPending}
              onClick={() => lock && unlock.mutate(lock.id)}
            >
              Unlock
            </Button>
          </Group>
        )}
      </Group>
      <div>
        <Text size="sm" c="dimmed">
          Source
        </Text>
        <Text style={{ whiteSpace: "pre-wrap" }}>{cue.source}</Text>
      </div>
      <Textarea
        label="Target"
        autosize
        minRows={2}
        maxRows={8}
        value={text}
        onChange={(event) => setDraft({ n: cue.n, text: event.currentTarget.value })}
        description="Saving locks this cue: later retranslations keep it as it is."
      />
      {save.isError && <Alert color="red">{save.error.message}</Alert>}
      {unlock.isError && <Alert color="red">{unlock.error.message}</Alert>}
      <Group justify="space-between">
        <Group gap="xs">
          <Button variant="default" disabled={index === 0} onClick={() => move(index - 1)}>
            Prev
          </Button>
          <Button variant="default" disabled={index >= rows.length - 1} onClick={() => move(index + 1)}>
            Next
          </Button>
        </Group>
        <Group gap="xs">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={save.isPending}
            disabled={text.trim() === "" || text === (cue.target ?? "")}
            onClick={() =>
              save.mutate(
                { lang, cue: cue.n, text },
                {
                  onSuccess: (answer) => {
                    onWarnings(answer.warnings ?? []);
                    setDraft(null);
                    onClose();
                  },
                },
              )
            }
          >
            Save
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}

/** Subtitles tab (wireframe 3a): source and target side by side; an edit is a sheet, never inline. */
export function Subtitles() {
  const { unit, target } = useOutletContext<UnitContext>();
  const lang = target?.lang ?? "";
  const [text, setText] = useState("");
  const [q] = useDebouncedValue(text, 300);
  const [jump, setJump] = useState("");
  const [at, setAt] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<number | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const cues = useCues(unit.id, { lang, q, offset, at, limit: PAGE }, lang !== "");
  const page = cues.data;
  const rows = page?.rows ?? [];
  // A search or a jump starts from its own answer: `offset` follows what the worker says, `at` is used once.
  const shown = rows.slice(0, PAGE);
  const from = page ? page.offset + 1 : 0;
  const to = page ? page.offset + shown.length : 0;
  const canEdit = target?.exists === true && page?.has_target === true;

  const go = (next: number) => {
    setAt("");
    setOffset(Math.max(0, next));
  };

  if (!target) return <Alert color="yellow">There is no target language to show.</Alert>;

  return (
    <Stack gap="md">
      <Group align="flex-end" wrap="wrap" gap="sm">
        <TextInput
          style={{ flex: "1 1 14rem" }}
          aria-label="Search text"
          placeholder="Search text…"
          leftSection={<IconSearch size={18} />}
          value={text}
          onChange={(event) => {
            setText(event.currentTarget.value);
            go(0);
          }}
        />
        <TextInput
          aria-label="Jump to time"
          placeholder="00:14:20"
          value={jump}
          error={jump !== "" && !isPlayerTime(jump) ? "Use 14:20 or 00:14:20" : undefined}
          onChange={(event) => setJump(event.currentTarget.value)}
          style={{ width: 150 }}
        />
        <Button
          variant="default"
          disabled={!isPlayerTime(jump)}
          onClick={() => {
            setText("");
            setAt(jump.trim());
          }}
        >
          Jump
        </Button>
      </Group>

      {warnings.length > 0 && (
        <Alert color="yellow" withCloseButton onClose={() => setWarnings([])} title="Saved, with a warning">
          {warnings.map((warning) => (
            <Text key={warning} size="sm">
              {warning}
            </Text>
          ))}
        </Alert>
      )}
      {!canEdit && page && !page.has_target && (
        <Alert color="blue">There is no target subtitle yet, so there is nothing to edit.</Alert>
      )}
      {page && !page.aligned && page.has_target && (
        <Alert color="yellow">The target has a different number of cues than the source: lines may not match.</Alert>
      )}
      {cues.isError && <Alert color="red">{cues.error.message}</Alert>}

      {cues.isPending && cues.fetchStatus !== "idle" ? (
        <Text c="dimmed" aria-label="Loading">
          Loading cues…
        </Text>
      ) : page && page.total === 0 ? (
        <Card withBorder padding="md">
          <Text>{q.trim() ? `No cue contains "${q.trim()}".` : "This subtitle has no cues."}</Text>
        </Card>
      ) : (
        <Stack component="ul" gap="xs" m={0} p={0} aria-label="Cues">
          {shown.map((cue, position) => (
            <Card
              key={cue.n}
              withBorder
              padding="sm"
              radius="md"
              component="li"
              style={{ listStyle: "none", borderColor: cue.n === page?.focus ? "var(--mantine-color-teal-6)" : undefined }}
            >
              <Stack gap={4}>
                <Group justify="space-between" wrap="nowrap">
                  <Text size="sm" c="dimmed">
                    Cue #{cue.n} · {formatMs(cue.start_ms)} → {formatMs(cue.end_ms)}
                  </Text>
                  {cue.locked && (
                    <Badge leftSection={<IconLock size={12} />} variant="light">
                      Locked
                    </Badge>
                  )}
                </Group>
                <Text style={{ whiteSpace: "pre-wrap" }}>
                  <Text span c="dimmed">
                    Source{" "}
                  </Text>
                  {cue.source}
                </Text>
                <Group justify="space-between" align="flex-start" wrap="nowrap">
                  <Text style={{ whiteSpace: "pre-wrap" }}>
                    <Text span c="dimmed">
                      Target{" "}
                    </Text>
                    {cue.target ?? "—"}
                  </Text>
                  {canEdit && cue.target !== null && (
                    <Button variant="default" aria-label={`Edit cue ${cue.n}`} onClick={() => setEditing(position)}>
                      Edit cue
                    </Button>
                  )}
                </Group>
              </Stack>
            </Card>
          ))}
        </Stack>
      )}

      {page && page.total > PAGE && (
        <Group justify="space-between">
          <Button variant="default" disabled={page.offset === 0} onClick={() => go(page.offset - PAGE)}>
            Previous
          </Button>
          <Text size="sm" c="dimmed">
            {from}–{to} of {page.total.toLocaleString()}
          </Text>
          <Button variant="default" disabled={to >= page.total} onClick={() => go(page.offset + PAGE)}>
            Next
          </Button>
        </Group>
      )}

      <Drawer
        opened={editing !== null}
        onClose={() => setEditing(null)}
        position="bottom"
        size="auto"
        padding="lg"
        radius="lg"
        title={editing !== null && shown[editing] ? `Edit cue #${shown[editing]!.n}` : "Edit cue"}
      >
        {editing !== null && (
          <CueSheet
            unitId={unit.id}
            lang={lang}
            rows={shown}
            index={editing}
            onMove={setEditing}
            onClose={() => setEditing(null)}
            onWarnings={setWarnings}
          />
        )}
      </Drawer>
    </Stack>
  );
}
