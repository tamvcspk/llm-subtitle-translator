import { Alert, Anchor, Badge, Button, Card, Checkbox, Drawer, Group, Loader, SimpleGrid, Stack, Text, Textarea, TextInput, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import {
  useCardImpact,
  useDeleteCharacter,
  useGlossary,
  useGlossaryDryRun,
  useOwnerStyle,
  useSaveCharacter,
  type StyleOwner,
} from "../api/queries";
import type { CharacterCard, GlossaryDoc, Style } from "../api/types";
import {
  addressFor,
  draftOf,
  people,
  plan,
  sameDraft,
  styleSummary,
  withEntry,
  withoutEntry,
  type Person,
  type PersonDraft,
} from "../lib/characters";
import { cleanDoc, glossaryImpact } from "../lib/glossary";
import { AXES } from "../lib/style";
import { ConfirmSheet } from "./ConfirmSheet";
import { AxisControl } from "./StyleForm";

const lower = (value: string) => value.trim().toLowerCase();

function Fact({ label, children }: { label: string; children: string | undefined | null }) {
  if (!children) return null;
  return (
    <Text size="sm">
      <Text span fw={600}>
        {label}
      </Text>{" "}
      {children}
    </Text>
  );
}

function PersonCard({ person, doc, onEdit, onDelete }: { person: Person; doc: GlossaryDoc; onEdit: () => void; onDelete: () => void }) {
  const { entry, card } = person;
  const forms = addressFor(doc, person.name);
  const style = card?.style ? styleSummary(card.style) : "";
  return (
    <Card withBorder padding="md" radius="md" component="section" aria-label={person.name}>
      <Stack gap="sm">
        <Stack gap={2}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Name
          </Text>
          <Title order={4}>{person.name}</Title>
          <Fact label="Translated">{entry?.target}</Fact>
          <Fact label="Gender">{entry?.gender}</Fact>
          <Fact label="Description">{entry?.description}</Fact>
        </Stack>

        <Stack gap={2}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Voice
          </Text>
          {card ? (
            <>
              <Fact label="Speech">{card.speech}</Fact>
              <Fact label="Aliases">{card.aliases?.join(", ")}</Fact>
              <Fact label="Catchphrases">{card.catchphrases?.map((line) => `“${line}”`).join(" ")}</Fact>
              <Fact label="Samples">{card.samples && card.samples.length > 0 ? `${card.samples.length} line${card.samples.length === 1 ? "" : "s"}` : ""}</Fact>
              <Fact label="Style">{style}</Fact>
              {card.always && <Badge variant="light" style={{ alignSelf: "flex-start" }}>Always in the prompt</Badge>}
            </>
          ) : (
            <Text size="sm" c="dimmed">
              No voice yet. Edit to say how they speak.
            </Text>
          )}
        </Stack>

        <Stack gap={2}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Forms of address (read only)
          </Text>
          {forms.length === 0 && !card?.address?.length && (
            <Text size="sm" c="dimmed">
              None set.
            </Text>
          )}
          {forms.map((form) => (
            <Text size="sm" key={form.with}>
              with {form.with}: {[form.says, form.hears].filter(Boolean).join(" / ")}
            </Text>
          ))}
          {(card?.address ?? []).map((item) => (
            <Text size="sm" key={`card-${item.to}`}>
              on the card, to {item.to}: {item.form}
            </Text>
          ))}
        </Stack>

        <Group justify="space-between">
          <Button variant="default" onClick={onEdit} aria-label={`Edit ${person.name}`}>
            Edit
          </Button>
          <Button variant="subtle" color="red" onClick={onDelete} aria-label={`Delete ${person.name}`}>
            Delete
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Stack gap="sm">
      <Text size="xs" fw={700} c="dimmed" tt="uppercase">
        {title}
      </Text>
      {children}
    </Stack>
  );
}

/** Add or Edit: the Name half goes into the glossary, the Voice half into the card; the effect of both is counted live. */
function PersonSheet({
  owner,
  lang,
  doc,
  all,
  person,
  effective,
  noun,
  onClose,
}: {
  owner: StyleOwner;
  lang: string;
  doc: GlossaryDoc;
  all: Person[];
  person: Person | null;
  effective: Style;
  noun: "episode" | "movie";
  onClose: () => void;
}) {
  const initial = useMemo(() => draftOf(person), [person]);
  const [draft, setDraft] = useState<PersonDraft>(initial);
  const [asking, setAsking] = useState(false);
  const [params] = useSearchParams();
  const save = useSaveCharacter(owner, lang);

  const name = draft.name.trim();
  const clash = name !== "" && all.some((other) => lower(other.name) === lower(name) && lower(other.name) !== lower(initial.name));
  const changed = !sameDraft(draft, initial);

  const parts = plan(draft, person);
  const glossary = parts.entry ? cleanDoc(withEntry(doc, parts.entry, person?.name ?? null)) : null;
  const card: { card: CharacterCard; replaces?: string } | null = parts.card
    ? { card: parts.card, ...(person?.card && person.card.name !== parts.card.name ? { replaces: person.card.name } : {}) }
    : null;
  const json = JSON.stringify({ glossary, card });
  const [settledJson] = useDebouncedValue(json, 400);
  const settled = useMemo(() => JSON.parse(settledJson) as { glossary: GlossaryDoc | null; card: { card: CharacterCard; replaces?: string } | null }, [settledJson]);
  const waiting = settledJson !== json;
  const ready = changed && name !== "" && !clash && !waiting;
  const forGlossary = useGlossaryDryRun(owner, lang, settled.glossary ? { doc: settled.glossary } : null, ready);
  const forCard = useCardImpact(owner, lang, settled.card, ready);

  const checking = ready && (forGlossary.isFetching || forCard.isFetching);
  const problem = forGlossary.error?.message ?? forCard.error?.message ?? null;
  const total = (forGlossary.data?.would_stale ?? 0) + (forCard.data?.would_stale ?? 0);
  const sentence = glossaryImpact({ would_stale: total }, "Saving", noun);

  const forms = addressFor(doc, name);
  const patch = (next: Partial<PersonDraft>) => setDraft({ ...draft, ...next });
  const text = (key: "name" | "target" | "gender" | "description" | "speech", label: string, extra: { required?: boolean } = {}) => (
    <TextInput label={label} required={extra.required} value={draft[key]} onChange={(event) => patch({ [key]: event.currentTarget.value })} />
  );
  const doSave = () => save.mutate({ glossary, card }, { onSuccess: onClose });
  const stylePatch = (style: Style) => patch({ style });
  const glossaryTab = `../glossary${params.get("lang") ? `?lang=${params.get("lang")}` : ""}`;

  return (
    <Drawer opened onClose={onClose} position="bottom" size="90%" title={person ? `Edit ${person.name}` : "Add character"} padding="lg" radius="lg">
      <Stack gap="lg">
        <Section title="Name">
          {text("name", "Source name", { required: true })}
          {clash && <Alert color="red">There is already a character called {name}.</Alert>}
          {text("target", "Translated name")}
          {text("gender", "Gender")}
          {text("description", "Description")}
        </Section>

        <Section title="Voice">
          {text("speech", "Speech")}
          <TextInput label="Aliases" description="Other names, separated by commas" value={draft.aliases} onChange={(event) => patch({ aliases: event.currentTarget.value })} />
          <Textarea label="Catchphrases" description="One per line" autosize minRows={2} value={draft.catchphrases} onChange={(event) => patch({ catchphrases: event.currentTarget.value })} />
          <Textarea label="Samples" description="Lines that sound like them, one per line" autosize minRows={2} value={draft.samples} onChange={(event) => patch({ samples: event.currentTarget.value })} />
          <Stack gap="sm">
            <Text size="sm" fw={500}>
              Style
            </Text>
            {AXES.filter((axis) => !axis.more).map((axis) => (
              <AxisControl key={axis.key} axis={axis} style={draft.style} effective={effective} onChange={stylePatch} />
            ))}
          </Stack>
          <Checkbox label="Always include in the prompt" checked={draft.always} onChange={(event) => patch({ always: event.currentTarget.checked })} />
        </Section>

        <Section title="Forms of address (read only)">
          {forms.length === 0 && draft.address.length === 0 && (
            <Text size="sm" c="dimmed">
              None set.
            </Text>
          )}
          {forms.map((form) => (
            <Text size="sm" key={form.with}>
              with {form.with}: {[form.says, form.hears].filter(Boolean).join(" / ")}
            </Text>
          ))}
          {draft.address.map((item) => (
            <Text size="sm" key={`card-${item.to}`}>
              on the card, to {item.to}: {item.form}
            </Text>
          ))}
          <Anchor component={Link} to={glossaryTab} size="sm" style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
            Manage in Forms of address →
          </Anchor>
        </Section>

        <Stack gap="xs">
          {changed && (
            <Badge color="yellow" variant="light" style={{ alignSelf: "flex-start" }}>
              Unsaved changes
            </Badge>
          )}
          <Text role="status" size="sm" c={problem ? "red" : "dimmed"}>
            {!changed ? "No changes." : name === "" ? "Give the character a name." : clash ? "" : waiting || checking ? "Checking…" : (problem ?? sentence)}
          </Text>
          {save.isError && !asking && <Alert color="red">{save.error.message}</Alert>}
          <Group justify="space-between">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button
              disabled={!changed || name === "" || clash || waiting || checking || problem !== null}
              loading={save.isPending && !asking}
              onClick={() => (total > 0 ? setAsking(true) : doSave())}
            >
              Save
            </Button>
          </Group>
        </Stack>
      </Stack>

      <ConfirmSheet
        opened={asking}
        title="Save character?"
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
    </Drawer>
  );
}

/** Delete both halves. The count comes from a dry run of the glossary alone: a card cannot be counted before it goes. */
function DeleteSheet({ owner, lang, doc, person, noun, onClose }: { owner: StyleOwner; lang: string; doc: GlossaryDoc; person: Person; noun: "episode" | "movie"; onClose: () => void }) {
  const remove = useDeleteCharacter(owner, lang);
  const without = person.entry ? cleanDoc(withoutEntry(doc, person.name)) : null;
  const impact = useGlossaryDryRun(owner, lang, without ? { doc: without } : null, true);
  const counted = glossaryImpact(impact.data, "Deleting", noun);
  const checking = without !== null && impact.isFetching;
  return (
    <ConfirmSheet
      opened
      title={`Delete ${person.name}?`}
      confirm="Confirm delete"
      pending={remove.isPending}
      error={remove.isError ? remove.error.message : null}
      onClose={onClose}
      onConfirm={() => remove.mutate({ glossary: without, card: person.card ? person.card.name : null }, { onSuccess: onClose })}
    >
      {checking
        ? "Checking…"
        : `${person.card ? "This removes the name and the voice. " : "This removes the name. "}${counted ?? "Translations made with this character's voice are flagged stale."} Nothing is retranslated.`}
    </ConfirmSheet>
  );
}

/**
 * The Characters tab of a series (wireframe 2d): one card per character with its Name (an entry of the glossary) and its
 * Voice (the character card). Forms of address are shown here but only edited in the glossary.
 */
export function OwnerCharacters({ owner, lang }: { owner: StyleOwner; lang: string }) {
  const glossary = useGlossary(owner, lang);
  const style = useOwnerStyle(owner, lang);
  const [editing, setEditing] = useState<{ person: Person | null } | null>(null);
  const [deleting, setDeleting] = useState<Person | null>(null);
  const noun = owner.kind === "movie" ? "movie" : "episode";

  if (glossary.isPending || style.isPending) return <Loader size="sm" aria-label="Loading characters" />;
  if (glossary.isError) return <Alert color="red">{glossary.error.message}</Alert>;
  if (style.isError) return <Alert color="red">{style.error.message}</Alert>;

  const doc = glossary.data.doc;
  const all = people(doc, style.data.cards);

  return (
    <Stack gap="md">
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Text size="sm" c="dimmed">
          A character is a name and a voice. Saving flags translations made without them stale; nothing is retranslated.
        </Text>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing({ person: null })}>
          Add character
        </Button>
      </Group>

      {all.length === 0 ? (
        <Card withBorder padding="md" radius="md">
          <Stack gap="sm" align="flex-start">
            <Text>No character cards yet. Add characters to define their names and voices.</Text>
            <Button variant="light" leftSection={<IconPlus size={16} />} onClick={() => setEditing({ person: null })}>
              Add character
            </Button>
          </Stack>
        </Card>
      ) : (
        <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
          {all.map((person) => (
            <PersonCard key={person.name} person={person} doc={doc} onEdit={() => setEditing({ person })} onDelete={() => setDeleting(person)} />
          ))}
        </SimpleGrid>
      )}

      {editing && (
        <PersonSheet
          key={editing.person?.name ?? "new"}
          owner={owner}
          lang={lang}
          doc={doc}
          all={all}
          person={editing.person}
          effective={style.data.effective}
          noun={noun}
          onClose={() => setEditing(null)}
        />
      )}
      {deleting && <DeleteSheet owner={owner} lang={lang} doc={doc} person={deleting} noun={noun} onClose={() => setDeleting(null)} />}
    </Stack>
  );
}
