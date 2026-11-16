import { Alert, Anchor, Button, Card, Checkbox, Group, SegmentedControl, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { Link, useOutletContext } from "react-router";
import { useCorrections, useCueTotal } from "../../api/queries";
import { EstimateSheet, type EstimateRequest } from "../../components/EstimateSheet";
import { KIND_LABEL, describeCorrection } from "../../lib/corrections";
import { buildRange, overwriteLine, type RangeScope } from "../../lib/retranslate";
import { episodeCode } from "../../lib/episodes";
import type { UnitContext } from "../Unit";

/** Retranslate tab (wireframe 3c): the whole episode or a range, with the corrections to use; the price comes first. */
export function Retranslate() {
  const { unit, target } = useOutletContext<UnitContext>();
  const lang = target?.lang ?? "";
  const corrections = useCorrections(unit.id, lang);
  const total = useCueTotal(unit.id, lang);
  const [scope, setScope] = useState<RangeScope>("episode");
  const [fields, setFields] = useState({ from: "", to: "" });
  const [off, setOff] = useState<ReadonlySet<number>>(new Set());
  const [problem, setProblem] = useState<string | null>(null);
  const [estimating, setEstimating] = useState<EstimateRequest | null>(null);

  if (!target) return <Alert color="yellow">There is no target language to retranslate.</Alert>;

  const usable = (corrections.data?.items ?? []).filter((item) => item.kind !== "cue_edit");
  const locked = (corrections.data?.items ?? []).filter((item) => item.kind === "cue_edit").map((item) => Number(item.payload.cue));
  const count = total.data?.total ?? null;
  const built = buildRange(scope, fields, count);
  const line = "range" in built ? overwriteLine(scope, built.range, count, locked) : null;
  const needsTarget = scope !== "episode" && !target.exists;
  const code = unit.series ? episodeCode(unit.season, unit.episode) : unit.title;

  const submit = () => {
    if ("problem" in built) {
      setProblem(built.problem);
      return;
    }
    setProblem(null);
    const label =
      scope === "cues" ? ` · cues ${fields.from}–${fields.to}` : scope === "time" ? ` · ${fields.from}–${fields.to}` : "";
    setEstimating({
      action: `Retranslate · ${code}${label}`,
      retranslate: {
        unit_id: unit.id,
        body: {
          lang,
          ...(built.range ? { range: built.range } : {}),
          ...(usable.length > 0 ? { correction_ids: usable.filter((item) => !off.has(item.id)).map((item) => item.id) } : {}),
        },
      },
    });
  };

  return (
    <Stack gap="md">
      <Card withBorder padding="md" radius="md">
        <Stack gap="sm">
          <Stack gap={4}>
            <Text size="sm" fw={500} id="range-scope">
              Scope
            </Text>
            <SegmentedControl
              aria-labelledby="range-scope"
              value={scope}
              onChange={(value) => {
                setScope(value as RangeScope);
                setProblem(null);
              }}
              data={[
                { value: "episode", label: "Whole episode" },
                { value: "cues", label: "Cue range" },
                { value: "time", label: "Time range" },
              ]}
            />
          </Stack>
          {scope !== "episode" && (
            <Group align="flex-end" wrap="wrap">
              <TextInput
                label={scope === "cues" ? "From cue #" : "From time"}
                placeholder={scope === "cues" ? "100" : "00:10:00"}
                value={fields.from}
                onChange={(event) => setFields({ ...fields, from: event.currentTarget.value })}
              />
              <TextInput
                label={scope === "cues" ? "To cue #" : "To time"}
                placeholder={scope === "cues" ? "150" : "00:15:00"}
                value={fields.to}
                onChange={(event) => setFields({ ...fields, to: event.currentTarget.value })}
              />
            </Group>
          )}
          {needsTarget && (
            <Alert color="yellow">Range retranslate needs an existing translation with the same number of cues.</Alert>
          )}

          <Stack gap="xs" component="fieldset" style={{ border: 0, margin: 0, padding: 0 }}>
            <Text component="legend" size="sm" fw={500}>
              Attach corrections ({usable.filter((item) => !off.has(item.id)).length})
            </Text>
            {usable.length === 0 ? (
              <Text size="sm" c="dimmed">
                No terms, forms of address or notes on this episode.{" "}
                <Anchor component={Link} to={`/unit/${unit.id}/corrections`} size="sm">
                  Add a correction
                </Anchor>
              </Text>
            ) : (
              usable.map((item) => (
                <Checkbox
                  key={item.id}
                  size="md"
                  label={`${KIND_LABEL[item.kind]} ${describeCorrection(item)}`}
                  checked={!off.has(item.id)}
                  onChange={() =>
                    setOff((previous) => {
                      const next = new Set(previous);
                      if (!next.delete(item.id)) next.add(item.id);
                      return next;
                    })
                  }
                />
              ))
            )}
          </Stack>

          {line && <Text>{line}</Text>}
          <Text size="sm" c="dimmed">
            The result replaces the current subtitle now; the old one stays in History.
          </Text>
          {problem && <Alert color="yellow">{problem}</Alert>}
          <Group justify="flex-end">
            <Button disabled={needsTarget || !unit.source_exists} onClick={submit}>
              Retranslate
            </Button>
          </Group>
        </Stack>
      </Card>
      <EstimateSheet request={estimating} onClose={() => setEstimating(null)} />
    </Stack>
  );
}
