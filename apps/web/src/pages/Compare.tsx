import { Alert, Anchor, Box, Button, Card, Checkbox, Drawer, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { IconChevronLeft } from "@tabler/icons-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useAdoptImpact, useChoose, useCompare, useUnit, useVersions } from "../api/queries";
import { EstimateSheet, type EstimateRequest } from "../components/EstimateSheet";
import { impactSentence } from "../lib/style";
import { judgeAsk, judgeNote, judgeStatus, styleLines, tally, verdicts, visibleRows } from "../lib/compare";
import { unitTitle } from "../lib/unit";

/** Compare (wireframe 4): the current translation against a candidate, cue by cue; pick cues or take the whole version. */
export function Compare() {
  const params = useParams();
  const unitId = Number(params.id);
  const version = Number(params.version);
  const [search] = useSearchParams();
  const unit = useUnit(unitId);
  const lang = search.get("lang") ?? unit.data?.targets[0]?.lang ?? "";
  const compare = useCompare(unitId, lang, version);
  const versions = useVersions(unitId, lang);
  const choose = useChoose(unitId, version);
  const [only, setOnly] = useState(true);
  const [picked, setPicked] = useState<ReadonlySet<number>>(new Set());
  const [asking, setAsking] = useState(false);
  const [withStyle, setWithStyle] = useState(true);
  const [judging, setJudging] = useState<EstimateRequest | null>(null);

  const variant = compare.data?.params.variant;
  const candidate = compare.data?.status === "candidate";
  const impact = useAdoptImpact(unitId, lang, version, asking && candidate && variant !== undefined && withStyle);

  if (unit.isPending || compare.isPending) return <Loader size="sm" aria-label="Loading comparison" />;
  if (unit.isError || compare.isError) {
    return <Alert color="red">{(unit.error ?? compare.error)?.message}</Alert>;
  }

  const data = compare.data;
  const current = versions.data?.versions.find((item) => item.current);
  const rows = visibleRows(data.rows, only);
  const { fromVariant, kept } = tally(data.rows, picked);
  const back = `/unit/${unitId}/history${lang ? `?lang=${lang}` : ""}`;
  const done = choose.data;
  const judge = data.judge;
  const ask = judge ? judgeAsk(judge) : null;

  const toggle = (n: number) =>
    setPicked((previous) => {
      const next = new Set(previous);
      if (!next.delete(n)) next.add(n);
      return next;
    });

  const closeAsk = () => {
    setAsking(false);
    choose.reset();
  };

  return (
    <Stack gap="md">
      <Stack gap="xs">
        <Anchor component={Link} to={back} size="sm" style={{ display: "inline-flex", alignItems: "center", minHeight: 44 }}>
          <IconChevronLeft size={16} /> {unitTitle(unit.data)}
        </Anchor>
        <Title order={2}>
          {current ? `Current v${current.version}` : "Current"} vs Candidate v{data.version}
          {variant ? ` "${variant.label}"` : ""}
        </Title>
        {!candidate && <Alert color="blue">This version is {data.status}, so it can only be looked at.</Alert>}
        {!data.aligned && <Alert color="yellow">The two translations do not have the same number of cues as the source: some lines may not line up.</Alert>}
        <Checkbox
          size="md"
          label={`Show only differing cues (${data.differing} of ${data.rows.length})`}
          checked={only}
          onChange={(event) => setOnly(event.currentTarget.checked)}
        />
      </Stack>

      {candidate && judge?.enabled && (
        <Card withBorder padding="sm" radius="md" component="section" aria-label="Judge">
          <Stack gap="xs">
            <Text size="sm" role="status">
              {judgeStatus(judge)}
            </Text>
            <Text size="xs" c="dimmed">
              Advice from {judge.model}, never a pick: only what you tick counts.
            </Text>
            {ask && (
              <Button
                variant="default"
                onClick={() =>
                  setJudging({
                    action: `Ask the judge · Candidate v${data.version}`,
                    judge: { unit_id: unitId, version, body: { lang, ...(ask.again ? { again: true } : {}) } },
                  })
                }
              >
                {ask.label}
              </Button>
            )}
          </Stack>
        </Card>
      )}

      {done && (
        <Alert color="teal">
          Done.{" "}
          <Anchor component={Link} to={back}>
            Back to History
          </Anchor>
        </Alert>
      )}

      {rows.length === 0 ? (
        <Card withBorder padding="md">
          <Text>{only ? "The two translations are the same." : "This episode has no cues."}</Text>
        </Card>
      ) : (
        <Stack component="ul" gap="xs" m={0} p={0} aria-label="Cues">
          {rows.map((row) => (
            <Card key={row.n} withBorder padding="sm" radius="md" component="li" style={{ listStyle: "none" }}>
              <Stack gap={4}>
                <Group justify="space-between" wrap="nowrap">
                  <Text size="sm" c="dimmed">
                    Cue #{row.n}
                  </Text>
                  {candidate && row.differs && (
                    <Checkbox
                      size="md"
                      label="Pick variant"
                      aria-label={`Pick variant for cue ${row.n}`}
                      checked={picked.has(row.n)}
                      onChange={() => toggle(row.n)}
                    />
                  )}
                </Group>
                <Text>
                  <Text span c="dimmed">
                    Source{" "}
                  </Text>
                  {row.source}
                </Text>
                <Text>
                  <Text span c="dimmed">
                    Current{" "}
                  </Text>
                  {row.current ?? "—"}
                </Text>
                <Text fw={row.differs ? 600 : 400}>
                  <Text span c="dimmed" fw={400}>
                    Variant{" "}
                  </Text>
                  {row.variant ?? "—"}
                </Text>
                {row.judge && (
                  <Text size="sm" c={row.judge.winner === "tie" ? "dimmed" : row.judge.winner === "variant" ? "teal" : "blue"}>
                    {judgeNote(row.judge)}
                  </Text>
                )}
              </Stack>
            </Card>
          ))}
        </Stack>
      )}

      {candidate && !done && (
        <Box
          style={{
            position: "sticky",
            bottom: 0,
            background: "var(--mantine-color-body)",
            borderTop: "1px solid var(--mantine-color-default-border)",
            paddingTop: 8,
            paddingBottom: "calc(8px + env(safe-area-inset-bottom, 0px))",
          }}
        >
          <Stack gap="xs">
            <Text size="sm" role="status">
              Selected {fromVariant} from variant, {kept} kept from current
            </Text>
            {choose.isError && !asking && <Alert color="red">{choose.error.message}</Alert>}
            <Group justify="space-between">
              <Button
                variant="default"
                disabled={fromVariant === 0}
                loading={choose.isPending && !asking}
                onClick={() => choose.mutate({ lang, picks: verdicts(data.rows, picked), apply: true })}
              >
                Apply picked cues
              </Button>
              <Button onClick={() => setAsking(true)}>Use this version</Button>
            </Group>
          </Stack>
        </Box>
      )}

      <EstimateSheet request={judging} onClose={() => setJudging(null)} />

      <Drawer opened={asking} onClose={closeAsk} position="bottom" size="auto" padding="lg" radius="lg" title={`Use candidate v${data.version} as the current translation?`}>
        <Stack>
          <Text>
            This replaces the current subtitle file{current ? `; v${current.version} stays in History` : ""}.
          </Text>
          {variant && (
            <Stack gap="xs">
              <Checkbox
                size="md"
                label="Also apply this variant's style to the series"
                checked={withStyle}
                onChange={(event) => setWithStyle(event.currentTarget.checked)}
              />
              {withStyle && (
                <Stack gap={2} pl="xl">
                  {styleLines(variant.preset, variant.style).map(([label, value]) => (
                    <Group key={label} gap="xs">
                      <Text size="sm" c="dimmed" w={110}>
                        {label}
                      </Text>
                      <Text size="sm">{value}</Text>
                    </Group>
                  ))}
                  <Text size="sm" role="status" c={impact.isError ? "red" : undefined}>
                    {impact.isPending || impact.isFetching
                      ? "Checking…"
                      : impact.isError
                        ? `The effect could not be counted: ${impact.error.message}`
                        : `${impactSentence(impact.data)} Nothing is retranslated.`}
                  </Text>
                </Stack>
              )}
            </Stack>
          )}
          {choose.isError && <Alert color="red">{choose.error.message}</Alert>}
          <Group justify="space-between">
            <Button variant="default" onClick={closeAsk}>
              Cancel
            </Button>
            <Button
              loading={choose.isPending}
              disabled={Boolean(variant) && withStyle && impact.isFetching}
              onClick={() =>
                choose.mutate(
                  { lang, whole: "variant", apply: true, ...(variant && withStyle ? { apply_style: true } : {}) },
                  { onSuccess: () => setAsking(false) },
                )
              }
            >
              Confirm
            </Button>
          </Group>
        </Stack>
      </Drawer>
    </Stack>
  );
}
