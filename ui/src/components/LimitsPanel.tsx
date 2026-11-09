import { formatCost, formatCount, formatDuration } from "../format";
import type { RunLimit, TaskActivity, TaskLimits, TaskStat } from "../types";
import { CollapsibleSection } from "./CollapsibleSection";

/**
 * "Limits & settings" (E-aYG5vn FR-3/FR-4, T-xNFBww): every effective run setting with its
 * source, and consumption against its cap where the backend could measure one.
 *
 * Renders whatever rows the backend sends -- later producers (e.g. sentinel caps and live
 * overrides) just append rows and they show up; absent rows are simply absent. A run that
 * predates the effective-settings snapshot shows a dash with an explanatory tooltip.
 */
export const DASH = "—";
export const PRE_UPGRADE_TIP =
  "Not recorded: this run started before settings and limits were captured.";
/** Bar turns amber/red at these fractions of the cap (text carries the meaning too). */
const WARN_FRACTION = 0.75;
const CRIT_FRACTION = 0.95;

const SOURCE_TITLES: Record<string, string> = {
  cli: "command-line flag",
  env: "environment variable",
  config: ".ao/config.yaml",
  default: "built-in default",
  unset: "not set",
  workflow: "workflow spec",
  overseer: "overseer config",
  run: "measured",
};

export function formatLimitValue(value: unknown, unit: string): string {
  if (value === null || value === undefined || value === "") return DASH;
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value !== "number") return String(value);
  if (unit === "usd") return formatCost(value);
  if (unit === "s") return formatDuration(value);
  if (unit === "tokens") return formatCount(value);
  return String(value);
}

/** used/cap in [0, +inf), or null when it cannot be computed. */
export function limitFraction(used: number | null, cap: number | null): number | null {
  if (used === null || cap === null || !(cap > 0)) return null;
  return Math.max(0, used / cap);
}

function barTone(fraction: number): "ok" | "warn" | "crit" {
  if (fraction >= CRIT_FRACTION) return "crit";
  return fraction >= WARN_FRACTION ? "warn" : "ok";
}

/** Progress bar of `used` against `cap`; the label always says the numbers (never color alone). */
export function UsageBar({
  used,
  cap,
  unit,
  label,
}: {
  used: number | null;
  cap: number | null;
  unit: string;
  label: string;
}) {
  const fraction = limitFraction(used, cap);
  const text = `${formatLimitValue(used, unit)} / ${formatLimitValue(cap, unit)}`;
  if (fraction === null) return <span className="num">{text}</span>;
  const pct = Math.round(fraction * 100);
  return (
    <span className="usage-bar-wrap">
      <span
        className={`usage-bar ${barTone(fraction)}`}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, pct)}
        aria-valuetext={`${text} (${pct}%)`}
      >
        <span className="usage-bar-fill" style={{ width: `${Math.min(100, pct)}%` }} />
      </span>
      <span className="num">
        {text} · {pct}%
      </span>
    </span>
  );
}

function LimitRow({ limit }: { limit: RunLimit }) {
  const measured = limit.cap !== null && limit.cap !== undefined;
  return (
    <tr data-testid={`limit-${limit.key}`}>
      <td>{limit.label}</td>
      <td>
        {measured || (limit.used !== null && limit.used !== undefined) ? (
          measured ? (
            <UsageBar
              used={limit.used ?? null}
              cap={limit.cap ?? null}
              unit={limit.unit}
              label={limit.label}
            />
          ) : (
            <span className="num">{formatLimitValue(limit.used, limit.unit)}</span>
          )
        ) : (
          <span className={limit.value === null || limit.value === undefined ? "muted" : ""}>
            {formatLimitValue(limit.value, limit.unit)}
          </span>
        )}
        {limit.note ? <span className="muted"> · {limit.note}</span> : null}
      </td>
      <td className="muted" title={SOURCE_TITLES[limit.source] ?? limit.source}>
        {limit.source || DASH}
      </td>
    </tr>
  );
}

export function LimitsPanel({
  limits,
  settingsRecorded,
}: {
  limits: RunLimit[] | undefined;
  settingsRecorded: boolean | undefined;
}) {
  const rows = limits ?? [];
  return (
    <CollapsibleSection id="limits" title="Limits & settings" testId="section-limits">
      {settingsRecorded ? null : (
        <p className="muted" title={PRE_UPGRADE_TIP} data-testid="limits-pre-upgrade">
          Settings: {DASH} <span style={{ fontSize: 12 }}>(not recorded for this run)</span>
        </p>
      )}
      {rows.length === 0 ? null : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Setting</th>
                <th>Value / usage vs cap</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((limit) => (
                <LimitRow key={limit.key} limit={limit} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CollapsibleSection>
  );
}

/** Effective per-task limits for the task detail panel (pre-upgrade task => dash + tooltip). */
export function TaskLimitsSection({
  stat,
  activity,
  nowMs,
}: {
  stat: TaskStat;
  activity?: TaskActivity | null;
  nowMs: number;
}) {
  const l: TaskLimits | null = stat.limits ?? null;
  if (l === null) {
    return (
      <section aria-label="Limits">
        <h3>Limits</h3>
        <p className="muted" title={PRE_UPGRADE_TIP} data-testid="task-limits-pre-upgrade">
          {DASH}
        </p>
      </section>
    );
  }
  const started = stat.started_at ? Date.parse(stat.started_at) : NaN;
  const elapsed =
    stat.status === "running" && !Number.isNaN(started)
      ? Math.max(0, (nowMs - started) / 1000)
      : stat.duration_seconds;
  return (
    <section aria-label="Limits">
      <h3>Limits</h3>
      <dl>
        <div>
          <dt>Thinking effort</dt>
          <dd>{l.thinking_effort ?? DASH}</dd>
        </div>
        <div>
          <dt>Turn budget</dt>
          <dd>{l.turn_budget ?? DASH}</dd>
        </div>
        <div>
          <dt>Turns</dt>
          <dd>
            <UsageBar
              used={activity?.turns ?? null}
              cap={l.max_turns}
              unit=""
              label="Turns used"
            />
          </dd>
        </div>
        <div>
          <dt>Autocompact</dt>
          <dd>{l.autocompact === null ? DASH : String(l.autocompact)}</dd>
        </div>
        <div>
          <dt>Timeout</dt>
          <dd>
            <UsageBar used={elapsed ?? null} cap={l.timeout_seconds} unit="s" label="Elapsed time" />
          </dd>
        </div>
        <div>
          <dt>Task cost</dt>
          <dd>
            {l.task_cost_cap_usd === null ? (
              formatCost(stat.cost_usd)
            ) : (
              <UsageBar used={stat.cost_usd} cap={l.task_cost_cap_usd} unit="usd" label="Task cost" />
            )}
          </dd>
        </div>
        <div>
          <dt>Attempts</dt>
          <dd>
            <UsageBar used={stat.attempts} cap={l.max_attempts} unit="" label="Attempts used" />
          </dd>
        </div>
      </dl>
    </section>
  );
}
