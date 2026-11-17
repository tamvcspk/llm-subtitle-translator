import { Alert, Button, Drawer, Group, Loader, Stack, Text, Textarea } from "@mantine/core";
import { useMemo, useRef, useState } from "react";
import { useJobSpec } from "../api/queries";

/**
 * Export job spec (UX §6.6): the bundle of a job's latest attempt, as a link the server serves (no `blob:`) and as text to
 * copy, since an iframe may refuse the clipboard. The bundle is only read while this box is open.
 */
export function JobSpecSheet({ jobId, name, onClose }: { jobId: string | null; name: string; onClose: () => void }) {
  const spec = useJobSpec(jobId);
  const box = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState<"yes" | "blocked" | null>(null);
  const text = useMemo(() => (spec.data === undefined ? "" : JSON.stringify(spec.data, null, 2)), [spec.data]);

  const close = () => {
    setCopied(null);
    onClose();
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied("yes");
    } catch {
      box.current?.select();
      setCopied("blocked");
    }
  };

  return (
    <Drawer opened={jobId !== null} onClose={close} position="bottom" size="auto" title="Export job spec" padding="lg" radius="lg">
      <Stack>
        <Text size="sm" c="dimmed">
          {name}
        </Text>
        {spec.isPending && jobId !== null && <Loader size="sm" aria-label="Reading the job spec" />}
        {spec.isError && <Alert color="yellow">{spec.error.message}</Alert>}
        {spec.data !== undefined && jobId !== null && (
          <>
            <Group>
              <Button component="a" href={`/api/jobs/${encodeURIComponent(jobId)}/spec`} download={`job-${jobId.slice(0, 8)}.json`}>
                Download job spec
              </Button>
              <Button variant="default" onClick={() => void copy()}>
                Copy spec
              </Button>
            </Group>
            {copied === "yes" && <Text size="sm">Copied.</Text>}
            {copied === "blocked" && <Text size="sm">Copying was blocked here. The text is selected: copy it by hand.</Text>}
            <Textarea
              ref={box}
              aria-label="Job spec"
              readOnly
              autosize
              minRows={6}
              maxRows={14}
              styles={{ input: { fontFamily: "monospace" } }}
              value={text}
              onFocus={(event) => event.currentTarget.select()}
            />
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
