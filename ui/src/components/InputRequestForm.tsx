import { useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import type { InputField, InputRequest, InputValue, RunInputsState } from "../types";
import { POLL_MS, usePolling } from "../usePolling";
import { ErrorBanner } from "./common";

/**
 * Awaiting-input UI (docs-md/awaiting-input-hld.md §5.3).
 *
 * Every string here (prompt, labels, choices, defaults) is task-authored and untrusted: it is
 * only ever a React text child or an attribute value, never HTML. The form is rendered from the
 * same structured request the CLI prompts from.
 */

type Draft = Record<string, InputValue>;

function initialDraft(fields: InputField[]): Draft {
  const draft: Draft = {};
  for (const f of fields) {
    if (f.default !== null && f.default !== undefined) draft[f.name] = f.default;
    else if (f.type === "bool") draft[f.name] = false;
    else if (f.type === "choice") draft[f.name] = "";
    else draft[f.name] = "";
  }
  return draft;
}

/** Values to send: optional empty text/choice fields are omitted so the server default applies. */
export function draftToValues(fields: InputField[], draft: Draft): Draft {
  const values: Draft = {};
  for (const f of fields) {
    const v = draft[f.name];
    if (v === undefined || (v === "" && !f.required)) continue;
    values[f.name] = v;
  }
  return values;
}

function missingRequired(fields: InputField[], draft: Draft): string | null {
  for (const f of fields) {
    if (f.required && f.type !== "bool" && (draft[f.name] ?? "") === "" && f.default == null) {
      return f.label || f.name;
    }
  }
  return null;
}

function FieldControl({
  field,
  value,
  disabled,
  onChange,
}: {
  field: InputField;
  value: InputValue;
  disabled: boolean;
  onChange: (value: InputValue) => void;
}) {
  const label = field.label || field.name;
  if (field.type === "bool") {
    return (
      <label className="row" style={{ gap: 8 }}>
        <input
          type="checkbox"
          checked={value === true}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        {label}
      </label>
    );
  }
  return (
    <label className="stack" style={{ gap: 4 }}>
      <span>
        {label}
        {field.required ? " *" : ""}
      </span>
      {field.type === "choice" ? (
        <select
          value={String(value)}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {String(value) === "" ? <option value="">Select…</option> : null}
          {field.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      ) : (
        <textarea
          rows={2}
          maxLength={field.max_chars}
          value={String(value)}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}

/** One open request: its prompt (plain text) and a form built from its fields. */
export function InputRequestForm({
  request,
  submitting,
  onSubmit,
}: {
  request: InputRequest;
  submitting: boolean;
  /** Resolves true when the answer was accepted. */
  onSubmit: (values: Draft) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<Draft>(() => initialDraft(request.fields));
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = async () => {
    const missing = missingRequired(request.fields, draft);
    if (missing) {
      setLocalError(`${missing} is required`);
      return;
    }
    setLocalError(null);
    await onSubmit(draftToValues(request.fields, draft));
  };

  return (
    <form
      className="stack input-request"
      style={{ gap: 8 }}
      data-testid={`input-request-${request.request_id}`}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="muted" style={{ fontSize: 12 }}>
        Task <span className="mono">{request.task_id}</span> asks:
      </div>
      <div className="input-request-prompt">{request.prompt}</div>
      <ErrorBanner message={localError} />
      {request.fields.map((field) => (
        <FieldControl
          key={field.name}
          field={field}
          value={draft[field.name] ?? ""}
          disabled={submitting}
          onChange={(value) => setDraft((prev) => ({ ...prev, [field.name]: value }))}
        />
      ))}
      <div className="row">
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Sending…" : "Answer & continue"}
        </button>
      </div>
    </form>
  );
}

/** Load (polled) and answer a run's input requests. */
export function useRunInputs(runId: string, waiting: boolean, onAnswered: () => void) {
  const [state, setState] = useState<RunInputsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.runInputs(runId);
      setState((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    } catch {
      // Best-effort like the notes box: an old backend (404) or a blip just hides the banner.
      setState(null);
    }
  }, [runId]);

  // `waiting` is in the tick identity so usePolling refetches the moment the run parks/unparks.
  const tick = useCallback(() => load(), [load, waiting]); // eslint-disable-line react-hooks/exhaustive-deps
  usePolling(tick, POLL_MS);

  const answer = useCallback(
    async (requestId: string, values: Draft) => {
      setSubmitting(true);
      setError(null);
      try {
        await api.answerInput(runId, requestId, { values, resume: true });
        await load();
        onAnswered();
        return true;
      } catch (err) {
        setError(errorMessage(err));
        await load();
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [runId, load, onAnswered],
  );

  return { state, error, submitting, answer };
}

/** "Needs your input" banner with one form per open request. Renders nothing when none are open. */
export function NeedsInputBanner({
  state,
  error,
  submitting,
  onAnswer,
}: {
  state: RunInputsState | null;
  error: string | null;
  submitting: boolean;
  onAnswer: (requestId: string, values: Draft) => Promise<boolean>;
}) {
  const open = (state?.requests ?? []).filter((r) => r.state === "open");
  if (open.length === 0) return null;
  return (
    <div className="banner needs-input stack" style={{ gap: 10 }} role="status" data-testid="needs-input-banner">
      <strong>Needs your input</strong>
      <ErrorBanner message={error} />
      {open.map((request) => (
        <InputRequestForm
          key={request.request_id}
          request={request}
          submitting={submitting}
          onSubmit={(values) => onAnswer(request.request_id, values)}
        />
      ))}
    </div>
  );
}
