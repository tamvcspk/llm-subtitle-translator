import { Alert, Button, Drawer, FileInput, Group, Loader, Radio, Select, Stack, Text, Textarea } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useRef, useState, type ReactNode } from "react";
import {
  useCatalog,
  useCopyGlossary,
  useGlossaryDryRun,
  useGlossaryHistory,
  useRestoreGlossary,
  useSaveGlossary,
  type StyleOwner,
} from "../api/queries";
import { glossaryImpact, NOTE_LABEL } from "../lib/glossary";

interface SheetProps {
  owner: StyleOwner;
  lang: string;
  opened: boolean;
  onClose: () => void;
}

const noun = (owner: StyleOwner) => (owner.kind === "movie" ? "movie" : "episode");

function Sheet({ opened, onClose, title, children }: { opened: boolean; onClose: () => void; title: string; children: ReactNode }) {
  return (
    <Drawer opened={opened} onClose={onClose} position="bottom" size="auto" title={title} padding="lg" radius="lg">
      <Stack>{children}</Stack>
    </Drawer>
  );
}

/** What each save replaced, newest first; Restore puts one back as a new save, after one question (it flags translations stale). */
export function HistorySheet({ owner, lang, opened, onClose, current }: SheetProps & { current: number }) {
  const history = useGlossaryHistory(owner, lang, opened);
  const restore = useRestoreGlossary(owner, lang);
  const [asking, setAsking] = useState<number | null>(null);

  const close = () => {
    setAsking(null);
    restore.reset();
    onClose();
  };

  if (asking !== null) {
    return (
      <Sheet opened={opened} onClose={close} title={`Restore v${asking}?`}>
        <Text>
          The glossary goes back to v{asking} as a new version. The current one stays in History. Translations made without these entries are
          flagged stale. Nothing is retranslated.
        </Text>
        {restore.isError && <Alert color="red">{restore.error.message}</Alert>}
        <Group justify="space-between">
          <Button variant="default" onClick={() => setAsking(null)}>
            Cancel
          </Button>
          <Button loading={restore.isPending} onClick={() => restore.mutate(asking, { onSuccess: close })}>
            Restore
          </Button>
        </Group>
      </Sheet>
    );
  }

  return (
    <Sheet opened={opened} onClose={close} title="Glossary history">
      {history.isPending && <Loader size="sm" aria-label="Loading history" />}
      {history.isError && <Alert color="red">{history.error.message}</Alert>}
      {history.data && (
        <Stack gap="xs" component="ul" style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Versions">
          {current > 0 && (
            <Group component="li" justify="space-between" wrap="nowrap" mih={44}>
              <Text>v{current}</Text>
              <Text c="dimmed">(current)</Text>
            </Group>
          )}
          {history.data.items.map((item) => (
            <Group component="li" key={item.version} justify="space-between" wrap="nowrap" mih={44} gap="sm">
              <Stack gap={0}>
                <Text>
                  v{item.version} · {new Date(item.at * 1000).toLocaleString()}
                </Text>
                <Text size="sm" c="dimmed">
                  {NOTE_LABEL[item.note] ?? item.note} · {item.terms} terms · {item.characters} characters · {item.address_rules} forms of address
                </Text>
              </Stack>
              <Button variant="default" onClick={() => setAsking(item.version)} aria-label={`Restore v${item.version}`}>
                Restore
              </Button>
            </Group>
          ))}
          {history.data.items.length === 0 && <Text c="dimmed">No earlier versions: nothing has been replaced yet.</Text>}
        </Stack>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={close}>
          Close
        </Button>
      </Group>
    </Sheet>
  );
}

/** Copy the glossary of another series: a copy, not a link. Merge keeps this series' entries when both have one. */
export function CopySheet({ owner, lang, opened, onClose }: SheetProps) {
  const catalog = useCatalog();
  const copy = useCopyGlossary(owner.id, lang);
  const [from, setFrom] = useState<string | null>(null);
  const [mode, setMode] = useState("merge");
  // `from_series` is the worker's own series id; the catalog lists the ones it keeps.
  const others = (catalog.data?.series ?? []).filter((series) => series.series_id !== null && series.series_id !== owner.id);

  const close = () => {
    copy.reset();
    onClose();
  };
  return (
    <Sheet opened={opened} onClose={close} title="Copy glossary from another series">
      <Select
        label="From"
        placeholder={others.length === 0 ? "No other series" : "Choose a series"}
        data={others.map((series) => ({ value: String(series.series_id), label: series.title }))}
        value={from}
        onChange={setFrom}
        searchable
        disabled={others.length === 0}
      />
      <Radio.Group value={mode} onChange={setMode} label="If both have an entry">
        <Stack gap="xs" mt="xs">
          <Radio value="merge" label="Merge (keep this series' entries on conflict)" />
          <Radio value="replace" label="Replace this glossary" />
        </Stack>
      </Radio.Group>
      <Text size="sm" c="dimmed">
        The current glossary stays in History. Translations made without the copied entries are flagged stale. Nothing is retranslated.
      </Text>
      {copy.isError && <Alert color="red">{copy.error.message}</Alert>}
      <Group justify="space-between">
        <Button variant="default" onClick={close}>
          Cancel
        </Button>
        <Button
          disabled={from === null}
          loading={copy.isPending}
          onClick={() => copy.mutate({ from_series: Number(from), merge: mode === "merge" }, { onSuccess: close })}
        >
          Copy
        </Button>
      </Group>
    </Sheet>
  );
}

/** Paste YAML or choose a file (it only fills the box); the effect is counted live and a broken file is refused with its path. */
export function ImportSheet({ owner, lang, opened, onClose }: SheetProps) {
  const [yaml, setYaml] = useState("");
  const [settled] = useDebouncedValue(yaml, 400);
  const blank = yaml.trim() === "";
  const waiting = settled !== yaml;
  const impact = useGlossaryDryRun(owner, lang, { yaml: settled }, opened && !blank && !waiting);
  const save = useSaveGlossary(owner, lang);
  const sentence = glossaryImpact(impact.data, "Importing", noun(owner));

  const close = () => {
    save.reset();
    setYaml("");
    onClose();
  };
  const choose = async (file: File | null) => {
    if (file) setYaml(await file.text());
  };

  return (
    <Sheet opened={opened} onClose={close} title="Import glossary YAML">
      <FileInput label="Choose file…" accept=".yaml,.yml,text/yaml,text/plain" clearable onChange={(file) => void choose(file)} />
      <Textarea
        label="Or paste it here"
        placeholder={"terms:\n  - source: Enforcer\n    target: Cảnh vệ"}
        autosize
        minRows={6}
        maxRows={14}
        styles={{ input: { fontFamily: "monospace" } }}
        value={yaml}
        onChange={(event) => setYaml(event.currentTarget.value)}
      />
      <Text role="status" size="sm" c={impact.isError ? "red" : "dimmed"}>
        {blank
          ? "Nothing to import yet."
          : waiting || impact.isFetching
            ? "Checking…"
            : impact.isError
              ? impact.error.message
              : `${sentence} The current glossary is kept in History.`}
      </Text>
      {save.isError && <Alert color="red">{save.error.message}</Alert>}
      <Group justify="space-between">
        <Button variant="default" onClick={close}>
          Cancel
        </Button>
        <Button disabled={blank || waiting || impact.isFetching || impact.isError} loading={save.isPending} onClick={() => save.mutate({ yaml }, { onSuccess: close })}>
          Import
        </Button>
      </Group>
    </Sheet>
  );
}

/** The saved glossary as a link the server serves (no `blob:`, UX §3) and as text to copy, since an iframe may refuse the clipboard. */
export function ExportSheet({ owner, lang, opened, onClose, yaml, unsaved }: SheetProps & { yaml: string; unsaved: boolean }) {
  const box = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState<"yes" | "blocked" | null>(null);
  const base = owner.kind === "movie" ? `/api/movies/${owner.id}` : `/api/series/${owner.id}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(yaml);
      setCopied("yes");
    } catch {
      box.current?.select();
      setCopied("blocked");
    }
  };
  const close = () => {
    setCopied(null);
    onClose();
  };

  return (
    <Sheet opened={opened} onClose={close} title="Export glossary YAML">
      {yaml === "" ? (
        <Text c="dimmed">There is nothing to export yet: save a glossary first.</Text>
      ) : (
        <>
          {unsaved && <Alert color="yellow">The file holds the saved glossary, not the unsaved changes in the form.</Alert>}
          <Group>
            <Button component="a" href={`${base}/glossary.yaml?lang=${encodeURIComponent(lang)}`} download>
              Download glossary YAML
            </Button>
            <Button variant="default" onClick={() => void copy()}>
              Copy YAML
            </Button>
          </Group>
          {copied === "yes" && <Text size="sm">Copied.</Text>}
          {copied === "blocked" && <Text size="sm">Copying was blocked here. The text is selected: copy it by hand.</Text>}
          <Textarea
            ref={box}
            aria-label="YAML"
            readOnly
            autosize
            minRows={6}
            maxRows={14}
            styles={{ input: { fontFamily: "monospace" } }}
            value={yaml}
            onFocus={(event) => event.currentTarget.select()}
          />
        </>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={close}>
          Close
        </Button>
      </Group>
    </Sheet>
  );
}
