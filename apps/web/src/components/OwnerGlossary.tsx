import { Alert, Badge, Button, Card, Checkbox, Group, Loader, Stack, Text, TextInput, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { useGlossary, useGlossaryDryRun, useSaveGlossary, type StyleOwner } from "../api/queries";
import type { GlossaryAddress, GlossaryAnswer, GlossaryDoc, GlossaryTerm } from "../api/types";
import { cleanDoc, glossaryImpact, patchAt, positionProblem, removeAt, sameDoc } from "../lib/glossary";
import { ConfirmSheet } from "./ConfirmSheet";
import { CopySheet, ExportSheet, HistorySheet, ImportSheet } from "./GlossarySheets";

type Sheet = "history" | "copy" | "import" | "export" | null;

function TermCard({ index, term, window, onChange, onDelete }: { index: number; term: GlossaryTerm; window: boolean; onChange: (patch: Partial<GlossaryTerm>) => void; onDelete: () => void }) {
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label={`Term ${index + 1}`}>
      <Stack gap="sm">
        <Group grow align="flex-start" wrap="wrap">
          <TextInput label="Source" value={term.source} onChange={(event) => onChange({ source: event.currentTarget.value })} style={{ flex: "1 1 14rem" }} />
          <TextInput label="Target" value={term.target} onChange={(event) => onChange({ target: event.currentTarget.value })} style={{ flex: "1 1 14rem" }} />
        </Group>
        {term.aliases && term.aliases.length > 0 && (
          <Text size="sm" c="dimmed">
            Also matches: {term.aliases.join(", ")}
          </Text>
        )}
        <Checkbox
          label="Locked"
          description="The subtitle must contain the target wherever the source appears."
          checked={term.locked ?? false}
          onChange={(event) => onChange({ locked: event.currentTarget.checked })}
        />
        <TextInput label="Note" value={term.note ?? ""} onChange={(event) => onChange({ note: event.currentTarget.value })} />
        {window && (
          <Group grow align="flex-start" wrap="wrap">
            <TextInput
              label="From episode"
              placeholder="S01E01"
              value={term.from ?? ""}
              error={positionProblem(term.from)}
              onChange={(event) => onChange({ from: event.currentTarget.value })}
              style={{ flex: "1 1 10rem" }}
            />
            <TextInput
              label="Until"
              placeholder="S02E01"
              value={term.until ?? ""}
              error={positionProblem(term.until)}
              onChange={(event) => onChange({ until: event.currentTarget.value })}
              style={{ flex: "1 1 10rem" }}
            />
          </Group>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" color="red" onClick={onDelete} aria-label={`Delete term ${index + 1}`}>
            Delete
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function AddressCard({
  index,
  rule,
  window,
  onChange,
  onDelete,
}: {
  index: number;
  rule: GlossaryAddress;
  window: boolean;
  onChange: (patch: Partial<GlossaryAddress>) => void;
  onDelete: () => void;
}) {
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label={`Form of address ${index + 1}`}>
      <Stack gap="sm">
        <Group grow align="flex-start" wrap="wrap">
          <TextInput label="Person A" value={rule.a} onChange={(event) => onChange({ a: event.currentTarget.value })} style={{ flex: "1 1 12rem" }} />
          <TextInput label="Person B" value={rule.b} onChange={(event) => onChange({ b: event.currentTarget.value })} style={{ flex: "1 1 12rem" }} />
        </Group>
        <Group grow align="flex-start" wrap="wrap">
          <TextInput
            label="A calls B"
            description="self–other, for example chị–em"
            value={rule.a_to_b ?? ""}
            onChange={(event) => onChange({ a_to_b: event.currentTarget.value })}
            style={{ flex: "1 1 12rem" }}
          />
          <TextInput
            label="B calls A"
            description="self–other"
            value={rule.b_to_a ?? ""}
            onChange={(event) => onChange({ b_to_a: event.currentTarget.value })}
            style={{ flex: "1 1 12rem" }}
          />
        </Group>
        {window && (
          <Group grow align="flex-start" wrap="wrap">
            <TextInput
              label="From"
              description="An episode or a time in it"
              placeholder="S01E01"
              value={rule.from ?? ""}
              error={positionProblem(rule.from)}
              onChange={(event) => onChange({ from: event.currentTarget.value })}
              style={{ flex: "1 1 12rem" }}
            />
            <TextInput
              label="Until"
              placeholder="S02E01"
              value={rule.until ?? ""}
              error={positionProblem(rule.until)}
              onChange={(event) => onChange({ until: event.currentTarget.value })}
              style={{ flex: "1 1 12rem" }}
            />
          </Group>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" color="red" onClick={onDelete} aria-label={`Delete form of address ${index + 1}`}>
            Delete
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function Editor({ owner, lang, answer }: { owner: StyleOwner; lang: string; answer: GlossaryAnswer }) {
  const movie = owner.kind === "movie";
  const noun = movie ? "movie" : "episode";
  const [draft, setDraft] = useState<GlossaryDoc>(answer.doc);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [asking, setAsking] = useState(false);
  const save = useSaveGlossary(owner, lang);

  const dirty = !sameDoc(draft, answer.doc);
  const clean = cleanDoc(draft);
  // Debounce the text of the document, which only changes when the document does.
  const json = JSON.stringify(clean);
  const [settledJson] = useDebouncedValue(json, 400);
  const settled = useMemo(() => JSON.parse(settledJson) as GlossaryDoc, [settledJson]);
  const waiting = dirty && settledJson !== json;
  const impact = useGlossaryDryRun(owner, lang, { doc: settled }, dirty && !waiting);
  const affected = impact.data?.would_stale ?? 0;
  const sentence = glossaryImpact(impact.data, "Saving", noun);

  const terms = draft.terms ?? [];
  const rules = draft.address_rules ?? [];
  // A movie has one subtitle: a window in story order means nothing there.
  const window = !movie;
  const doSave = () => save.mutate({ doc: clean }, { onSuccess: () => setAsking(false) });
  const close = () => setSheet(null);

  return (
    <Stack gap="lg">
      <Stack gap="sm">
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Text fw={600}>{answer.exists ? `Version v${answer.version}` : "No glossary yet"}</Text>
          <Group gap="xs" wrap="wrap">
            <Button variant="default" onClick={() => setSheet("history")}>
              History
            </Button>
            {!movie && (
              <Button variant="default" onClick={() => setSheet("copy")}>
                Copy from another series
              </Button>
            )}
            <Button variant="default" onClick={() => setSheet("import")}>
              Import YAML
            </Button>
            <Button variant="default" onClick={() => setSheet("export")}>
              Export YAML
            </Button>
          </Group>
        </Group>
        <Text size="sm" c="dimmed">
          The glossary is a hard rule: it wins over every style. Saving flags translations made without it stale; nothing is retranslated.
        </Text>
      </Stack>

      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={4}>Terms</Title>
          <Button variant="light" leftSection={<IconPlus size={16} />} onClick={() => setDraft({ ...draft, terms: [...terms, { source: "", target: "" }] })}>
            Add term
          </Button>
        </Group>
        {terms.length === 0 && <Text c="dimmed">No terms yet. A term makes a name or a word come out the same way every time.</Text>}
        {terms.map((term, index) => (
          <TermCard
            key={index}
            index={index}
            term={term}
            window={window}
            onChange={(patch) => setDraft({ ...draft, terms: patchAt(terms, index, patch) })}
            onDelete={() => setDraft({ ...draft, terms: removeAt(terms, index) })}
          />
        ))}
      </Stack>

      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={4}>Forms of address</Title>
          <Button variant="light" leftSection={<IconPlus size={16} />} onClick={() => setDraft({ ...draft, address_rules: [...rules, { a: "", b: "" }] })}>
            Add address rule
          </Button>
        </Group>
        {rules.length === 0 && <Text c="dimmed">No forms of address yet. They say who calls whom how, from which episode on.</Text>}
        {rules.map((rule, index) => (
          <AddressCard
            key={index}
            index={index}
            rule={rule}
            window={window}
            onChange={(patch) => setDraft({ ...draft, address_rules: patchAt(rules, index, patch) })}
            onDelete={() => setDraft({ ...draft, address_rules: removeAt(rules, index) })}
          />
        ))}
      </Stack>

      <Stack gap="xs">
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Stack gap={2}>
            {dirty && (
              <Badge color="yellow" variant="light" style={{ alignSelf: "flex-start" }}>
                Unsaved changes
              </Badge>
            )}
            <Text role="status" size="sm" c={impact.isError ? "red" : "dimmed"}>
              {!dirty ? "No changes." : waiting || impact.isFetching ? "Checking…" : impact.isError ? impact.error.message : sentence}
            </Text>
          </Stack>
          <Group gap="xs">
            <Button variant="default" disabled={!dirty} onClick={() => setDraft(answer.doc)}>
              Discard
            </Button>
            <Button
              disabled={!dirty || waiting || impact.isFetching || impact.isError}
              loading={save.isPending && !asking}
              onClick={() => (affected > 0 ? setAsking(true) : doSave())}
            >
              Save
            </Button>
          </Group>
        </Group>
        {save.isError && !asking && <Alert color="red">{save.error.message}</Alert>}
        {save.isSuccess && !dirty && (
          <Text size="sm" c="teal.7">
            Saved. Nothing was retranslated.
          </Text>
        )}
      </Stack>

      <ConfirmSheet
        opened={asking}
        title="Save glossary?"
        confirm="Confirm"
        pending={save.isPending}
        error={save.isError ? save.error.message : null}
        onClose={() => {
          setAsking(false);
          save.reset();
        }}
        onConfirm={doSave}
      >
        {`${sentence} Nothing is retranslated.`}
      </ConfirmSheet>

      <HistorySheet owner={owner} lang={lang} opened={sheet === "history"} onClose={close} current={answer.version} />
      {!movie && <CopySheet owner={owner} lang={lang} opened={sheet === "copy"} onClose={close} />}
      <ImportSheet owner={owner} lang={lang} opened={sheet === "import"} onClose={close} />
      <ExportSheet owner={owner} lang={lang} opened={sheet === "export"} onClose={close} yaml={answer.yaml} unsaved={dirty} />
    </Stack>
  );
}

/**
 * Terms and Forms of address of a series or a movie (wireframe 2c): the hard rules that win over every style. Characters
 * live on their own tab; their entries in this document are kept as they are. The form starts again from what the
 * worker says after every save, restore, copy or import.
 */
export function OwnerGlossary({ owner, lang }: { owner: StyleOwner; lang: string }) {
  const glossary = useGlossary(owner, lang);
  // A query without a language is disabled and stays pending for good: say so instead of spinning.
  if (lang === "") return <Alert color="blue">There is no target language to work in: add one in Settings, Translation, then come back.</Alert>;
  if (glossary.isPending) return <Loader size="sm" aria-label="Loading glossary" />;
  if (glossary.isError) return <Alert color="red">{glossary.error.message}</Alert>;
  return <Editor key={JSON.stringify([glossary.data.version, glossary.data.doc])} owner={owner} lang={lang} answer={glossary.data} />;
}
