import { Alert, Badge, Button, Card, Group, SegmentedControl, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { useOutletContext } from "react-router";
import { useAddCorrection, useCorrections, useRemoveCorrection, type CorrectionAnswer, type CorrectionScope } from "../../api/queries";
import { ago } from "../../lib/format";
import { KIND_LABEL, buildPayload, describeCorrection, scopesFor, type CorrectionKind } from "../../lib/corrections";
import type { UnitContext } from "../Unit";

const KINDS: CorrectionKind[] = ["term", "address", "note", "cue_edit"];

function Fields({ kind, values, set }: { kind: CorrectionKind; values: Record<string, string>; set: (name: string, value: string) => void }) {
  const input = (name: string, label: string, placeholder?: string) => (
    <TextInput
      key={name}
      label={label}
      placeholder={placeholder}
      value={values[name] ?? ""}
      onChange={(event) => set(name, event.currentTarget.value)}
      style={{ flex: "1 1 12rem" }}
    />
  );
  switch (kind) {
    case "term":
      return <Group align="flex-end">{[input("source", "Term in the source", "Council"), input("target", "Translation", "Hội Đồng")]}</Group>;
    case "address":
      return (
        <Stack gap="xs">
          <Group align="flex-end">{[input("a", "Person A", "Vi"), input("b", "Person B", "Caitlyn")]}</Group>
          <Group align="flex-end">
            {[input("a_to_b", "A calls B", "chị – em"), input("b_to_a", "B calls A", "em – chị"), input("from_cue", "From cue # (optional)")]}
          </Group>
        </Stack>
      );
    case "note":
      return (
        <Textarea
          label="Note for the translator"
          placeholder="The scene at the bridge is whispered"
          autosize
          minRows={2}
          value={values.text ?? ""}
          onChange={(event) => set("text", event.currentTarget.value)}
        />
      );
    case "cue_edit":
      return (
        <Group align="flex-end">
          {input("cue", "Cue #")}
          <Textarea
            label="New line"
            autosize
            minRows={1}
            value={values.text ?? ""}
            onChange={(event) => set("text", event.currentTarget.value)}
            style={{ flex: "2 1 16rem" }}
          />
        </Group>
      );
  }
}

function resultNote(answer: CorrectionAnswer): string {
  if (answer.version !== undefined) return `Saved as version ${answer.version}; the cue is locked.`;
  if (answer.scope && answer.scope !== "episode") {
    const n = answer.impact?.would_stale ?? 0;
    const flagged = n === 0 ? "No translation is flagged stale." : `${n} ${n === 1 ? "translation is" : "translations are"} flagged stale. Nothing is retranslated.`;
    return `Added to the glossary. ${flagged}`;
  }
  return "Added. It is used the next time this episode is translated.";
}

/** Corrections tab (wireframe 3b): add a term, a form of address, a note or a cue edit; the episode's own are listed. */
export function Corrections() {
  const { unit, target } = useOutletContext<UnitContext>();
  const lang = target?.lang ?? "";
  const list = useCorrections(unit.id, lang);
  const add = useAddCorrection(unit.id);
  const remove = useRemoveCorrection(unit.id);
  const [kind, setKind] = useState<CorrectionKind>("term");
  const [scope, setScope] = useState<CorrectionScope>("episode");
  const [values, setValues] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<CorrectionAnswer | null>(null);

  if (!target) return <Alert color="yellow">There is no target language to correct.</Alert>;

  const scopes = scopesFor(kind, unit.memory_applicable);
  const chosenScope = scopes.some((item) => item.value === scope) ? scope : "episode";

  const submit = () => {
    const built = buildPayload(kind, values);
    if ("problem" in built) {
      setProblem(built.problem);
      return;
    }
    setProblem(null);
    add.mutate(
      { lang, kind, scope: chosenScope, payload: built.payload },
      {
        onSuccess: (answer) => {
          setDone(answer);
          setValues({});
        },
      },
    );
  };

  return (
    <Stack gap="md">
      <Card withBorder padding="md" radius="md">
        <Stack gap="sm">
          <Text fw={600}>Add a correction</Text>
          <SegmentedControl
            aria-label="Kind"
            value={kind}
            onChange={(value) => {
              setKind(value as CorrectionKind);
              setProblem(null);
              setDone(null);
            }}
            data={KINDS.map((item) => ({ value: item, label: KIND_LABEL[item] }))}
          />
          <Fields kind={kind} values={values} set={(name, value) => setValues((previous) => ({ ...previous, [name]: value }))} />
          <Group justify="space-between" align="flex-end" wrap="wrap">
            <Stack gap={4}>
              <Text size="sm" fw={500} id="scope-label">
                Scope
              </Text>
              <SegmentedControl
                aria-labelledby="scope-label"
                value={chosenScope}
                onChange={(value) => setScope(value as CorrectionScope)}
                data={scopes}
                disabled={scopes.length === 1}
              />
            </Stack>
            <Button loading={add.isPending} onClick={submit}>
              Add
            </Button>
          </Group>
          {kind === "cue_edit" && (
            <Text size="sm" c="dimmed">
              A cue edit changes the file now, with no model call.
            </Text>
          )}
          {problem && <Alert color="yellow">{problem}</Alert>}
          {add.isError && <Alert color="red">{add.error.message}</Alert>}
          {done && !add.isError && <Alert color="teal">{resultNote(done)}</Alert>}
          {done?.warnings?.map((warning) => (
            <Alert key={warning} color="yellow">
              {warning}
            </Alert>
          ))}
        </Stack>
      </Card>

      {list.isError && <Alert color="red">{list.error.message}</Alert>}
      {remove.isError && <Alert color="red">{remove.error.message}</Alert>}
      {list.isPending ? (
        <Text c="dimmed" aria-label="Loading">
          Loading corrections…
        </Text>
      ) : list.data && list.data.items.length === 0 ? (
        <Card withBorder padding="md">
          <Text c="dimmed">No corrections on this episode yet. Terms and forms of address for the whole series are in its Glossary.</Text>
        </Card>
      ) : (
        <Stack component="ul" gap="xs" m={0} p={0} aria-label="Corrections">
          {list.data?.items.map((item) => (
            <Card key={item.id} withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
              <Group justify="space-between" wrap="nowrap" align="center">
                <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                  <Badge variant="light">{KIND_LABEL[item.kind]}</Badge>
                  <Text style={{ minWidth: 0 }}>{describeCorrection(item)}</Text>
                  <Text size="sm" c="dimmed" style={{ flexShrink: 0 }}>
                    · Episode · {ago(item.created_at)}
                  </Text>
                </Group>
                <Button
                  variant="default"
                  aria-label={`Remove ${KIND_LABEL[item.kind]} ${describeCorrection(item)}`}
                  loading={remove.isPending && remove.variables === item.id}
                  onClick={() => remove.mutate(item.id)}
                >
                  Remove
                </Button>
              </Group>
            </Card>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
