import { useState } from "react";
import { FEEDBACK_REASONS, MAX_NOTE_CHARS, noteError } from "../format";
import type { FeedbackRating, FeedbackReason, FeedbackRequest } from "../types";

const RATINGS: { value: FeedbackRating; glyph: string }[] = [
  { value: "good", glyph: "👍" },
  { value: "ok", glyph: "👌" },
  { value: "bad", glyph: "👎" },
];

/**
 * Rating form shared by the run-level and per-task controls: rating buttons, reason tags and
 * a note. Pure presentation — `onSubmit` owns the request, `disabled` freezes it while posting.
 * The note is only ever sent as plain text; it is never rendered back as HTML here.
 */
export function FeedbackForm({
  label,
  scope,
  taskId,
  disabled,
  onSubmit,
}: {
  label: string;
  scope: "run" | "task";
  taskId?: string;
  disabled: boolean;
  onSubmit: (body: FeedbackRequest) => void | Promise<void>;
}) {
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [note, setNote] = useState("");

  const lengthError = noteError(note);
  const canSubmit = rating !== null && !lengthError && !disabled;

  const toggleReason = (reason: FeedbackReason) =>
    setReasons((current) =>
      current.includes(reason) ? current.filter((r) => r !== reason) : [...current, reason],
    );

  const submit = async () => {
    if (!rating || lengthError) return;
    const body: FeedbackRequest = { scope, rating, reasons };
    if (scope === "task" && taskId) body.task_id = taskId;
    if (note.trim()) body.note = note.trim();
    await onSubmit(body);
    setRating(null);
    setReasons([]);
    setNote("");
  };

  return (
    <div className="stack" style={{ gap: 8 }} role="group" aria-label={label}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {RATINGS.map((option) => (
          <button
            key={option.value}
            type="button"
            disabled={disabled}
            aria-pressed={rating === option.value}
            className={rating === option.value ? "primary" : undefined}
            onClick={() => setRating(option.value)}
          >
            <span aria-hidden="true">{option.glyph} </span>
            {option.value}
          </button>
        ))}
      </div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {FEEDBACK_REASONS.map((reason) => (
          <button
            key={reason}
            type="button"
            disabled={disabled}
            aria-pressed={reasons.includes(reason)}
            className={reasons.includes(reason) ? "primary" : undefined}
            onClick={() => toggleReason(reason)}
          >
            {reasons.includes(reason) ? "✓ " : ""}
            {reason}
          </button>
        ))}
      </div>
      <textarea
        aria-label={`${label} note`}
        style={{ minHeight: 60 }}
        placeholder="Optional note (plain text)"
        value={note}
        disabled={disabled}
        onChange={(event) => setNote(event.target.value)}
      />
      <div className="row" style={{ gap: 8 }}>
        <span className="muted" style={{ fontSize: 11 }}>
          {note.trim().length}/{MAX_NOTE_CHARS}
        </span>
        {lengthError ? (
          <span role="alert" style={{ fontSize: 12 }}>
            ⚠ {lengthError}
          </span>
        ) : null}
        <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>
          Submit {scope === "run" ? "run rating" : "task rating"}
        </button>
      </div>
    </div>
  );
}
