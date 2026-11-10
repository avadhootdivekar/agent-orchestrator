import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { InputRequestForm, NeedsInputBanner, draftToValues } from "../components/InputRequestForm";
import { RunDetail } from "../components/RunDetail";
import { statusGlyph, statusTone } from "../format";
import type { InputRequest } from "../types";

const HOSTILE = "<img src=x onerror=alert(1)> which db?";

const REQUEST: InputRequest = {
  request_id: "q-pick-1",
  task_id: "pick",
  state: "open",
  prompt: HOSTILE,
  fields: [
    { name: "db", type: "choice", label: "Database <b>", choices: ["postgres", "sqlite"], default: "sqlite", max_chars: 4000, required: true },
    { name: "note", type: "text", label: "Notes", choices: [], default: null, max_chars: 20, required: false },
    { name: "ok", type: "bool", label: "Proceed?", choices: [], default: true, max_chars: 4000, required: true },
  ],
  created_at: "2026-10-21T10:00:00Z",
  answered_at: null,
  answer: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("awaiting_input status", () => {
  it("is its own tone and glyph, not failed", () => {
    expect(statusTone("awaiting_input")).toBe("waiting");
    expect(statusTone("awaiting_input")).not.toBe(statusTone("failed"));
    expect(statusGlyph("awaiting_input")).toBe("?");
  });
});

describe("InputRequestForm", () => {
  it("renders the prompt and labels as plain text, never HTML", () => {
    const { container } = render(
      <InputRequestForm request={REQUEST} submitting={false} onSubmit={async () => true} />,
    );
    expect(screen.getByText(HOSTILE)).toBeTruthy();
    expect(screen.getByText(/Database <b>/)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("renders select / checkbox / textarea with defaults and submits chosen values", async () => {
    const onSubmit = vi.fn(async () => true);
    render(<InputRequestForm request={REQUEST} submitting={false} onSubmit={onSubmit} />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("sqlite");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["postgres", "sqlite"]);
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    fireEvent.change(select, { target: { value: "postgres" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Answer & continue" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    // optional empty text field is omitted so the server default applies
    expect(onSubmit).toHaveBeenCalledWith({ db: "postgres", ok: false });
  });

  it("blocks submit locally when a required field without default is empty", async () => {
    const req: InputRequest = {
      ...REQUEST,
      fields: [{ name: "who", type: "text", label: "Who", choices: [], default: null, max_chars: 50, required: true }],
    };
    const onSubmit = vi.fn(async () => true);
    render(<InputRequestForm request={req} submitting={false} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Answer & continue" }));
    expect(await screen.findByText("Who is required")).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("draftToValues keeps required empties out only when optional", () => {
    expect(draftToValues(REQUEST.fields, { db: "sqlite", note: "", ok: true })).toEqual({ db: "sqlite", ok: true });
  });
});

describe("NeedsInputBanner", () => {
  it("renders nothing when no request is open", () => {
    const { container } = render(
      <NeedsInputBanner
        state={{ run_id: "r", waiting_for_input: false, requests: [{ ...REQUEST, state: "answered" }] }}
        error={null}
        submitting={false}
        onAnswer={async () => true}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the banner with a form per open request and passes the request id", async () => {
    const onAnswer = vi.fn(async () => true);
    render(
      <NeedsInputBanner
        state={{ run_id: "r", waiting_for_input: true, requests: [REQUEST] }}
        error={null}
        submitting={false}
        onAnswer={onAnswer}
      />,
    );
    expect(screen.getByText("Needs your input")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Answer & continue" }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith("q-pick-1", { db: "sqlite", ok: true }));
  });
});

describe("api", () => {
  it("answerInput posts values + resume to the encoded run/request route", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify({ request: REQUEST, resumed: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fn);
    await api.answerInput("run 1", "q-pick-1", { values: { db: "sqlite" }, resume: true });
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/runs/run%201/inputs/q-pick-1");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ values: { db: "sqlite" }, resume: true });
  });
});

describe("RunDetail with a waiting run", () => {
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("shows the banner, answers via the route, then refetches", async () => {
    let answered = false;
    const posts: unknown[] = [];
    const task = {
      id: "pick", status: "awaiting_input", attempts: 1, started_at: null, ended_at: null,
      duration_seconds: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, origin: "static",
      route: null, output_artifact_path: null, outputs: [], integration_status: null,
      tier_reached: null, conflicted_count: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
      cache_hit_rate: null,
    };
    const detail = () => ({
      summary: { run_id: "run-1", workflow_id: "demo", status: "running",
        started_at: "2026-10-21T10:00:00+00:00", updated_at: "2026-10-21T10:05:00+00:00",
        task_count: 1, task_counts: { awaiting_input: 1 }, cost_usd: 0, input_tokens: 0, output_tokens: 0,
        wall_seconds: 1, active_seconds: 1, is_terminal: false, is_live: false, launch_id: null,
        waiting_for_input: !answered },
      tasks: [task], tripped_breakers: [], route_decisions: {}, monitor_decisions: [], run_dir: "/ws/run-1",
      is_live: false, launch: null, integration: null,
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/inputs/q-pick-1")) {
        posts.push(JSON.parse(String(init?.body)));
        answered = true;
        return json({ request: { ...REQUEST, state: "answered" }, resumed: true }, 200);
      }
      if (url.endsWith("/inputs")) {
        return json({ run_id: "run-1", waiting_for_input: !answered, requests: [{ ...REQUEST, state: answered ? "answered" : "open" }] });
      }
      if (url.endsWith("/notes")) return json({ run_id: "run-1", accepting: false, max_chars: 2000, max_notes: 100, notes: [] });
      if (url.endsWith("/feedback")) return json({ entries: [] });
      if (url.includes("/log")) return json({ run_id: "run-1", launch_id: null, text: "" });
      return json(detail());
    }));
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    expect(await screen.findByTestId("needs-input-banner")).toBeTruthy();
    expect(screen.getAllByText("awaiting_input").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Answer & continue" }));
    await waitFor(() => expect(posts).toEqual([{ values: { db: "sqlite", ok: true }, resume: true }]));
    await waitFor(() => expect(screen.queryByTestId("needs-input-banner")).toBeNull());
  });
});
