import { useEffect, useState } from "react";
import { api } from "../api";
import type { RunOptionSchema, RunOptions, RunOptionsSchema } from "../types";

/**
 * Schema-driven launch-options form (E-aYG5vn FR-7).
 *
 * Every field is generated from `GET /api/schema/run-options` (the server's run-options
 * registry): control type from `type`/`choices`, label/group from `ui`, hint from `help`.
 * Nothing here names an individual option. Used by New run, Template launch and Resume.
 *
 * Degradation: a backend without the endpoint (or a failed fetch) gets FALLBACK_SCHEMA — the
 * pre-registry field set expressed as the same data, so it renders through the same code. A
 * newer backend's unknown option type renders as a text field, an unknown group is appended
 * after the known ones, and options with `ui: null` are not shown.
 */

/** Group display order (matches the registry's UI_GROUP_ORDER); unknown groups follow. */
export const GROUP_ORDER = [
  "Model & reasoning",
  "Turns & retries",
  "Concurrency & budget",
  "Quota",
  "Recovery",
  "Sentinel",
];
/** Groups expanded by default; the rest are collapsed to keep the form short. */
const OPEN_GROUPS = new Set([GROUP_ORDER[0]]);
const FALLBACK_GROUP = "Overrides";

/** Option set of a backend that predates the schema endpoint (what the form used to hard-wire). */
export const FALLBACK_SCHEMA: RunOptionsSchema = {
  schema: "fallback",
  options: [
    { name: "model", type: "str", ui: { group: FALLBACK_GROUP, label: "Model" } },
    {
      name: "effort",
      type: "enum",
      choices: ["low", "medium", "high"],
      ui: { group: FALLBACK_GROUP, label: "Effort" },
    },
    {
      name: "max_parallel",
      type: "int",
      default: 1,
      ui: { group: FALLBACK_GROUP, label: "Max parallel" },
    },
    {
      name: "max_attempts",
      type: "int",
      ui: { group: FALLBACK_GROUP, label: "Max attempts" },
    },
    {
      name: "budget_total",
      type: "int",
      ui: { group: FALLBACK_GROUP, label: "Token budget" },
    },
  ],
};

/** Loads the registry schema once per mount; falls back (never throws) on any failure. */
export function useRunOptionsSchema(): RunOptionsSchema | null {
  const [schema, setSchema] = useState<RunOptionsSchema | null>(null);
  useEffect(() => {
    let live = true;
    api
      .runOptionsSchema()
      .then((s) => live && setSchema(Array.isArray(s?.options) ? s : FALLBACK_SCHEMA))
      .catch(() => live && setSchema(FALLBACK_SCHEMA));
    return () => {
      live = false;
    };
  }, []);
  return schema;
}

/** Options the form shows, grouped in display order. */
export function groupOptions(
  schema: RunOptionsSchema,
): { group: string; options: RunOptionSchema[] }[] {
  const byGroup = new Map<string, RunOptionSchema[]>();
  for (const opt of schema.options) {
    if (!opt.ui) continue;
    const list = byGroup.get(opt.ui.group) ?? [];
    list.push(opt);
    byGroup.set(opt.ui.group, list);
  }
  const known = GROUP_ORDER.filter((g) => byGroup.has(g));
  const extra = [...byGroup.keys()].filter((g) => !GROUP_ORDER.includes(g));
  return [...known, ...extra].map((group) => ({ group, options: byGroup.get(group)! }));
}

/** Smallest accepted number for a numeric option (registry counts/seconds are all >= 1). */
const MIN_INT = 1;

/** Validation message for a typed value, or null when acceptable. */
export function validateOption(opt: RunOptionSchema, value: string | number | boolean): string | null {
  if (opt.type === "int") {
    const n = Number(value);
    return Number.isInteger(n) && n >= MIN_INT ? null : `Must be a whole number >= ${MIN_INT}`;
  }
  if (opt.type === "float") {
    const n = Number(value);
    return Number.isFinite(n) && n >= 0 ? null : "Must be a number >= 0";
  }
  if (opt.type === "enum" && opt.choices && opt.choices.length > 0) {
    return opt.choices.includes(String(value)) ? null : `Must be one of ${opt.choices.join(", ")}`;
  }
  return null;
}

/** name -> message for every currently-set option that fails validation (empty = submittable). */
export function optionErrors(
  schema: RunOptionsSchema | null,
  value: RunOptions,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const opt of schema?.options ?? []) {
    if (!opt.ui || !(opt.name in value)) continue;
    const message = validateOption(opt, value[opt.name]);
    if (message) errors[opt.name] = message;
  }
  return errors;
}

/** Raw control string -> typed wire value; "" means "unset" (key dropped). */
function coerce(opt: RunOptionSchema, raw: string): string | number | boolean | undefined {
  if (raw === "") return undefined;
  if (opt.type === "int" || opt.type === "float") return Number(raw);
  if (opt.type === "bool") return raw === "true";
  return raw;
}

function controlValue(value: RunOptions, name: string): string {
  return name in value ? String(value[name]) : "";
}

function OptionField({
  opt,
  value,
  error,
  onSet,
}: {
  opt: RunOptionSchema;
  value: RunOptions;
  error?: string;
  onSet: (opt: RunOptionSchema, raw: string) => void;
}) {
  const id = `opt-${opt.name.replace(/[^A-Za-z0-9_-]/g, "-")}`;
  const current = controlValue(value, opt.name);
  const defaultText = opt.default !== null && opt.default !== undefined ? String(opt.default) : "";
  const set = (raw: string) => onSet(opt, raw);
  const providers = opt.providers && opt.providers.length > 0 ? opt.providers.join(", ") : "";
  let control;
  if (opt.type === "bool") {
    control = (
      <select id={id} value={current} onChange={(e) => set(e.target.value)}>
        <option value="">{defaultText ? `default (${defaultText === "true" ? "on" : "off"})` : "default"}</option>
        <option value="true">on</option>
        <option value="false">off</option>
      </select>
    );
  } else if (opt.type === "enum") {
    control = (
      <select id={id} value={current} onChange={(e) => set(e.target.value)}>
        <option value="">default</option>
        {(opt.choices ?? []).map((choice) => (
          <option key={choice} value={choice}>
            {opt.choice_labels?.[choice] ?? choice}
          </option>
        ))}
      </select>
    );
  } else if (opt.type === "int" || opt.type === "float") {
    control = (
      <input
        id={id}
        type="number"
        min={opt.type === "int" ? MIN_INT : 0}
        step={opt.type === "int" ? 1 : "any"}
        value={current}
        placeholder={defaultText || "default"}
        onChange={(e) => set(e.target.value)}
      />
    );
  } else {
    // "str" and any type a newer server adds: a text field is always safe (server validates).
    control = (
      <input
        id={id}
        value={current}
        placeholder={defaultText || "default"}
        onChange={(e) => set(e.target.value)}
      />
    );
  }
  return (
    <div>
      <label htmlFor={id} title={opt.help || undefined}>
        {opt.ui?.label ?? opt.name}
      </label>
      {control}
      {error ? (
        <div className="field-hint" role="alert">
          {error}
        </div>
      ) : null}
      {providers ? <div className="field-hint">applies to: {providers}</div> : null}
    </div>
  );
}

export function RunOptionsForm({
  schema,
  value,
  onChange,
}: {
  schema: RunOptionsSchema | null;
  value: RunOptions;
  onChange: (next: RunOptions) => void;
}) {
  if (!schema) return <div className="muted">Loading options…</div>;
  const errors = optionErrors(schema, value);
  const onSet = (opt: RunOptionSchema, raw: string) => {
    const next = { ...value };
    const typed = coerce(opt, raw);
    if (typed === undefined) delete next[opt.name];
    else next[opt.name] = typed;
    onChange(next);
  };
  return (
    <div className="run-options" data-testid="run-options">
      {groupOptions(schema).map(({ group, options }, index) => (
        <details
          key={group}
          className="options-group"
          open={OPEN_GROUPS.has(group) || group === FALLBACK_GROUP || (index === 0 && !GROUP_ORDER.includes(group))}
        >
          <summary>{group}</summary>
          <div className="options-grid">
            {options.map((opt) => (
              <OptionField
                key={opt.name}
                opt={opt}
                value={value}
                error={errors[opt.name]}
                onSet={onSet}
              />
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
