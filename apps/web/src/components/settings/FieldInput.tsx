import { NumberInput, Select, SegmentedControl, Stack, Switch, Text } from "@mantine/core";
import { useMemo } from "react";
import type { ModelChoice } from "../../api/queries";
import { fieldProblem, type Field } from "../../lib/settings";

const zones = (): string[] => {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [];
  }
};

/** One setting as the form shows it: the kind of the field says which control it is (UX Appendix B). */
export function FieldInput({
  field,
  value,
  onChange,
  models,
}: {
  field: Field;
  value: unknown;
  onChange: (value: unknown) => void;
  models: ModelChoice[];
}) {
  const problem = fieldProblem(field, value);
  const zoneList = useMemo(() => zones(), []);

  switch (field.kind) {
    case "switch":
      return (
        <Switch
          label={field.label}
          description={field.hint}
          checked={value === true}
          onChange={(event) => onChange(event.currentTarget.checked)}
          styles={{ root: { minHeight: 44, display: "flex", alignItems: "center" } }}
        />
      );
    case "choice": {
      const labelId = `field-${field.key}`;
      return (
        <Stack gap={4}>
          <Text size="sm" fw={500} id={labelId}>
            {field.label}
          </Text>
          <div style={{ overflowX: "auto" }}>
            <SegmentedControl aria-labelledby={labelId} data={field.options ?? []} value={String(value ?? "")} onChange={onChange} />
          </div>
          {field.hint && (
            <Text size="xs" c="dimmed">
              {field.hint}
            </Text>
          )}
        </Stack>
      );
    }
    case "model": {
      const current = typeof value === "string" ? value : "";
      const ids = models.map((model) => model.id);
      const data = [...new Set([...(current && !ids.includes(current) ? [current] : []), ...ids])];
      return (
        <Select
          label={field.label}
          description={field.hint}
          placeholder={field.empty}
          data={data}
          value={current || null}
          onChange={(next) => onChange(next ?? "")}
          clearable={field.empty !== undefined}
          searchable
          allowDeselect={field.empty !== undefined}
          nothingFoundMessage="No such model"
        />
      );
    }
    case "zone": {
      const current = typeof value === "string" ? value : "";
      const data = [...new Set([...(current ? [current] : []), ...zoneList])];
      return (
        <Select
          label={field.label}
          description={field.hint}
          data={data}
          value={current || null}
          onChange={(next) => next && onChange(next)}
          searchable
          allowDeselect={false}
        />
      );
    }
    default: {
      // int, float and the two numbers the worker keeps as text
      const decimal = field.kind !== "int";
      return (
        <NumberInput
          label={field.label}
          description={field.hint}
          value={typeof value === "string" && value.trim() !== "" ? Number(value) : (value as number | "")}
          onChange={(next) => onChange(next === "" ? "" : next)}
          allowDecimal={decimal}
          allowNegative={false}
          decimalScale={decimal ? 4 : 0}
          step={field.step}
          min={field.min}
          max={field.max}
          hideControls
          error={problem}
          inputMode={decimal ? "decimal" : "numeric"}
        />
      );
    }
  }
}
