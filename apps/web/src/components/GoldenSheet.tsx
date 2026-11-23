import { Alert, Button, Drawer, Group, Stack, Text, Textarea } from "@mantine/core";
import { useRef, useState } from "react";
import { fetchGolden } from "../api/queries";
import { routeUrl } from "../api/route";
import { goldenFileName, preview } from "../lib/insights";

export interface GoldenTarget {
  seriesId: number;
  seriesKey: string;
  title: string;
  lang: string;
}

type Copy = { state: "idle" } | { state: "copying" } | { state: "copied"; cues: number } | { state: "blocked"; text: string; cues: number } | { state: "failed"; message: string };

/**
 * The golden set of one series and language (UX §6.6): the file as a link the server serves (no `blob:`), and a Copy that reads
 * the same text and writes it to the clipboard. Where an iframe refuses the clipboard, the text is shown in a box that selects
 * all; only its first lines are drawn, but copying out of it still gives every cue. Read-only, so no Estimate.
 */
export function GoldenSheet({ target, onClose }: { target: GoldenTarget | null; onClose: () => void }) {
  const [copy, setCopy] = useState<Copy>({ state: "idle" });
  const box = useRef<HTMLTextAreaElement>(null);

  const close = () => {
    setCopy({ state: "idle" });
    onClose();
  };

  const copyText = async () => {
    if (!target) return;
    setCopy({ state: "copying" });
    let text: string;
    let cues: number;
    try {
      const golden = await fetchGolden(target.seriesId, target.lang);
      text = JSON.stringify(golden, null, 2);
      cues = golden.cues.length;
    } catch (error) {
      setCopy({ state: "failed", message: error instanceof Error ? error.message : String(error) });
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopy({ state: "copied", cues });
    } catch {
      setCopy({ state: "blocked", text, cues });
      queueMicrotask(() => box.current?.select());
    }
  };

  const file = target ? goldenFileName(target.seriesKey, target.lang) : "";
  const href = target
    ? routeUrl("/api/series/{series_id}/golden", { path: { series_id: target.seriesId }, query: { lang: target.lang, download: "1" } })
    : "";

  return (
    <Drawer opened={target !== null} onClose={close} position="bottom" size="auto" title={target ? `Golden set ${target.lang} · ${target.title}` : ""} padding="lg" radius="lg">
      <Stack>
        <Group>
          <Button component="a" href={href} aria-label={`Download ${file}`}>
            {`Download ${file}`}
          </Button>
          <Button variant="default" onClick={() => void copyText()} loading={copy.state === "copying"}>
            Copy
          </Button>
        </Group>
        {copy.state === "copying" && <Text size="sm" role="status">Copying…</Text>}
        {copy.state === "copied" && <Text size="sm" role="status">{`Copied ${copy.cues.toLocaleString()} cues.`}</Text>}
        {copy.state === "failed" && <Alert color="red">{`Could not copy: ${copy.message}`}</Alert>}
        {copy.state === "blocked" && (
          <>
            <Text size="sm" role="status">Copying was blocked here. The text is selected: copy it by hand.</Text>
            <Textarea
              ref={box}
              aria-label="Golden set"
              readOnly
              autosize
              minRows={6}
              maxRows={10}
              styles={{ input: { fontFamily: "monospace" } }}
              value={preview(copy.text)}
              onFocus={(event) => event.currentTarget.select()}
              onCopy={(event) => {
                // Only the first lines are drawn; what is copied out is every cue.
                event.clipboardData.setData("text/plain", copy.text);
                event.preventDefault();
              }}
            />
            <Text size="xs" c="dimmed">{`Select all and copy still gives all ${copy.cues.toLocaleString()} cues; only the start is shown.`}</Text>
          </>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={close}>
            Close
          </Button>
        </Group>
      </Stack>
    </Drawer>
  );
}
