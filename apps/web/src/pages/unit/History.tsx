import { Alert, Badge, Button, Card, Drawer, Group, Loader, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link, useOutletContext } from "react-router";
import { useAdopt, useDiff, useRevert, useVersions } from "../../api/queries";
import type { Version } from "../../api/types";
import { ConfirmSheet } from "../../components/ConfirmSheet";
import { versionActions, versionDetails, versionHeadline, versionNote } from "../../lib/history";
import type { UnitContext } from "../Unit";

function DiffSheet({ unitId, lang, version, onClose }: { unitId: number; lang: string; version: number | null; onClose: () => void }) {
  const diff = useDiff(unitId, lang, version);
  return (
    <Drawer opened={version !== null} onClose={onClose} position="bottom" size="80%" padding="lg" radius="lg" title={version === null ? "" : `Changes in v${version}`}>
      <Stack>
        {diff.isPending && <Loader size="sm" aria-label="Loading changes" />}
        {diff.isError && <Alert color="red">{diff.error.message}</Alert>}
        {diff.data && (
          <>
            <Text size="sm" c="dimmed">
              {diff.data.changed_cues === 0
                ? "The two versions are the same."
                : `${diff.data.changed_cues} ${diff.data.changed_cues === 1 ? "cue differs" : "cues differ"} from ${diff.data.against === null ? "the start" : `v${diff.data.against}`}.`}
            </Text>
            {diff.data.changes.map((change, position) => (
              <Card key={position} withBorder padding="sm" radius="md">
                <Stack gap={4}>
                  <Text size="sm" fw={600}>
                    Cue #{(change.after[0] ?? change.before[0])?.n}
                  </Text>
                  {change.before.map((cue) => (
                    <Text key={`b${cue.n}`} c="red.8" style={{ whiteSpace: "pre-wrap" }}>
                      − {cue.text}
                    </Text>
                  ))}
                  {change.after.map((cue) => (
                    <Text key={`a${cue.n}`} c="teal.8" style={{ whiteSpace: "pre-wrap" }}>
                      + {cue.text}
                    </Text>
                  ))}
                </Stack>
              </Card>
            ))}
          </>
        )}
      </Stack>
    </Drawer>
  );
}

/** History tab (wireframe 3d): every version of the file, newest first; Diff, Restore and Use this version. */
export function History() {
  const { unit, target } = useOutletContext<UnitContext>();
  const lang = target?.lang ?? "";
  const versions = useVersions(unit.id, lang);
  const revert = useRevert(unit.id);
  const adopt = useAdopt(unit.id);
  const [diffOf, setDiffOf] = useState<number | null>(null);
  const [asking, setAsking] = useState<{ kind: "restore" | "use"; version: Version } | null>(null);

  if (!target) return <Alert color="yellow">There is no target language to show.</Alert>;
  if (versions.isPending) return <Loader size="sm" aria-label="Loading versions" />;
  if (versions.isError) return <Alert color="red">{versions.error.message}</Alert>;

  const list = versions.data.versions;
  const action = asking?.kind === "restore" ? revert : adopt;
  const close = () => {
    revert.reset();
    adopt.reset();
    setAsking(null);
  };

  return (
    <Stack gap="md">
      {versions.data.modified_outside && <Alert color="yellow">The file on disk is not the current version: it changed outside the worker.</Alert>}
      {list.length === 0 ? (
        <Card withBorder padding="md">
          <Text c="dimmed">No version yet: nothing has been translated into this language.</Text>
        </Card>
      ) : (
        <Stack component="ul" gap="xs" m={0} p={0} aria-label="Versions">
          {list.map((item) => {
            const actions = versionActions(item);
            const note = versionNote(item);
            return (
              <Card key={item.version} withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
                <Stack gap={6}>
                  <Group gap="xs" wrap="wrap">
                    <Text fw={700}>v{item.version}</Text>
                    {item.current && <Badge color="teal">Current</Badge>}
                    {item.status === "candidate" && <Badge color="grape">Candidate</Badge>}
                    <Text size="sm">{versionHeadline(item)}</Text>
                  </Group>
                  <Text size="sm" c="dimmed">
                    {versionDetails(item)}
                  </Text>
                  {item.params.variant && <Text size="sm">Variant "{item.params.variant.label}"</Text>}
                  {note && (
                    <Text size="sm" c="dimmed">
                      {note}
                    </Text>
                  )}
                  {actions.length > 0 && (
                    <Group gap="xs" wrap="wrap">
                      {actions.includes("diff") && (
                        <Button variant="default" aria-label={`Diff v${item.version}`} onClick={() => setDiffOf(item.version)}>
                          Diff
                        </Button>
                      )}
                      {actions.includes("compare") && (
                        <Button
                          component={Link}
                          to={`/unit/${unit.id}/compare/${item.version}?lang=${lang}`}
                          variant="default"
                          aria-label={`Compare v${item.version}`}
                        >
                          Compare
                        </Button>
                      )}
                      {actions.includes("use") && (
                        <Button variant="light" aria-label={`Use v${item.version}`} onClick={() => setAsking({ kind: "use", version: item })}>
                          Use this version
                        </Button>
                      )}
                      {actions.includes("restore") && (
                        <Button variant="light" aria-label={`Restore v${item.version}`} onClick={() => setAsking({ kind: "restore", version: item })}>
                          Restore
                        </Button>
                      )}
                    </Group>
                  )}
                </Stack>
              </Card>
            );
          })}
        </Stack>
      )}

      <DiffSheet unitId={unit.id} lang={lang} version={diffOf} onClose={() => setDiffOf(null)} />
      <ConfirmSheet
        opened={asking !== null}
        title={asking?.kind === "restore" ? `Restore v${asking.version.version}?` : `Use v${asking?.version.version ?? ""}?`}
        confirm={asking?.kind === "restore" ? "Restore" : "Use this version"}
        pending={action.isPending}
        error={action.error?.message ?? null}
        onClose={close}
        onConfirm={() =>
          asking && action.mutate({ version: asking.version.version, lang }, { onSuccess: close })
        }
      >
        {asking?.kind === "restore"
          ? "The subtitle is written back as a new version, and what is on disk now stays in History. No model call is made."
          : "The subtitle on disk is replaced by this version, and the one on disk now stays in History. Only this episode changes."}
      </ConfirmSheet>
    </Stack>
  );
}
