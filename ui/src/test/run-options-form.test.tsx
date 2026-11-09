import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NewRun } from "../components/NewRun";
import {
  groupOptions,
  optionErrors,
  RunOptionsForm,
  validateOption,
} from "../components/RunOptionsForm";
import { TemplateLaunch } from "../components/TemplateLaunch";
import type { RunOptions, RunOptionsSchema, TemplateInfo } from "../types";

/** A registry-shaped schema covering every control type + a group/type the UI has never seen. */
const SCHEMA: RunOptionsSchema = {
  schema: "ao.run-options/v1",
  options: [
    { name: "model", type: "str", ui: { group: "Model & reasoning", label: "Model" } },
    {
      name: "turn_budget",
      type: "enum",
      choices: ["low", "high"],
      choice_labels: { low: "low (15 turns)", high: "high (60 turns)" },
      ui: { group: "Model & reasoning", label: "Turn budget" },
    },
    {
      name: "autocompact",
      type: "str",
      providers: ["claude-cli"],
      ui: { group: "Model & reasoning", label: "Autocompact" },
    },
    {
      name: "max_attempts",
      type: "int",
      ui: { group: "Turns & retries", label: "Max attempts" },
    },
    {
      name: "self_heal",
      type: "bool",
      default: false,
      ui: { group: "Recovery", label: "Self-heal" },
    },
    {
      name: "sentinel.llm.budget_usd",
      type: "float",
      ui: { group: "Sentinel", label: "Sentinel LLM budget (USD)" },
    },
    { name: "hidden", type: "str", ui: null },
    { name: "future_knob", type: "quantum", ui: { group: "Brand New", label: "Future knob" } },
  ],
};

function Harness({ onValue }: { onValue: (v: RunOptions) => void }) {
  const [value, setValue] = useState<RunOptions>({});
  return (
    <RunOptionsForm
      schema={SCHEMA}
      value={value}
      onChange={(v) => {
        setValue(v);
        onValue(v);
      }}
    />
  );
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("groupOptions / validateOption", () => {
  it("orders known groups first, appends unknown ones, drops ui:null", () => {
    const groups = groupOptions(SCHEMA).map((g) => g.group);
    expect(groups).toEqual([
      "Model & reasoning",
      "Turns & retries",
      "Recovery",
      "Sentinel",
      "Brand New",
    ]);
    expect(groupOptions(SCHEMA).flatMap((g) => g.options).map((o) => o.name)).not.toContain(
      "hidden",
    );
  });

  it("validates ints, floats and enums", () => {
    const [int, , , , float] = [
      SCHEMA.options[3],
      0,
      0,
      0,
      SCHEMA.options[5],
    ] as never[];
    expect(validateOption(int, 3)).toBeNull();
    expect(validateOption(int, 0)).not.toBeNull();
    expect(validateOption(int, 1.5)).not.toBeNull();
    expect(validateOption(float, 0.5)).toBeNull();
    expect(validateOption(float, -1)).not.toBeNull();
    expect(validateOption(SCHEMA.options[1], "medium")).not.toBeNull();
    expect(optionErrors(SCHEMA, { max_attempts: 0, model: "x" })).toEqual({
      max_attempts: expect.any(String),
    });
  });
});

describe("RunOptionsForm", () => {
  it("renders each registry option with the right control and typed values", async () => {
    const seen: RunOptions[] = [];
    render(<Harness onValue={(v) => seen.push(v)} />);

    // control types
    expect(screen.getByLabelText("Model").tagName).toBe("INPUT");
    const tb = screen.getByLabelText("Turn budget");
    expect(tb.tagName).toBe("SELECT");
    expect(within(tb).getByRole("option", { name: "high (60 turns)" })).toBeInTheDocument();
    expect(screen.getByLabelText("Max attempts")).toHaveAttribute("type", "number");
    expect(screen.getByLabelText("Self-heal").tagName).toBe("SELECT");
    expect(screen.getByLabelText("Sentinel LLM budget (USD)")).toHaveAttribute("step", "any");
    expect(screen.getByText("applies to: claude-cli")).toBeInTheDocument();
    // unknown future type degrades to a text field; hidden option is absent
    expect(screen.getByLabelText("Future knob")).not.toHaveAttribute("type", "number");
    expect(screen.queryByLabelText("hidden")).toBeNull();

    // only the first group is expanded by default
    const open = (name: string) =>
      screen.getByText(name).closest("details")!.hasAttribute("open");
    expect(open("Model & reasoning")).toBe(true);
    expect(open("Turns & retries")).toBe(false);

    await userEvent.selectOptions(tb, "high");
    await userEvent.type(screen.getByLabelText("Max attempts"), "2");
    await userEvent.selectOptions(screen.getByLabelText("Self-heal"), "false");
    await userEvent.type(screen.getByLabelText("Sentinel LLM budget (USD)"), "1.5");
    expect(seen[seen.length - 1]).toEqual({
      turn_budget: "high",
      max_attempts: 2,
      self_heal: false,
      "sentinel.llm.budget_usd": 1.5,
    });
    await userEvent.selectOptions(tb, "");
    expect(seen[seen.length - 1]).not.toHaveProperty("turn_budget");
  });

  it("shows an inline error for an invalid number", async () => {
    render(<Harness onValue={() => {}} />);
    await userEvent.type(screen.getByLabelText("Max attempts"), "0");
    expect(await screen.findByRole("alert")).toHaveTextContent(/whole number/);
  });
});

const WORKFLOWS = [
  {
    id: "wf",
    name: "wf",
    path: "/ws/wf.json",
    task_count: 1,
    prompt_path: "prompt.md",
    general_instructions: [],
    error: null,
  },
];

const TEMPLATE: TemplateInfo = {
  name: "t1",
  description: "d",
  path: "/p",
  source: "builtin",
  params: [],
  required_agents: [],
  prompt_skeleton: null,
};

function stubApi(opts: { schema: unknown | null; posts: { url: string; body: any }[] }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        opts.posts.push({ url, body: JSON.parse(String(init.body)) });
        return jsonResponse(
          url.includes("/templates/")
            ? {
                instance_dir: "/d",
                workflow_path: "/d/workflow.json",
                workflow: WORKFLOWS[0],
                launch: {
                  launch_id: "launch-1",
                  run_id: "r1",
                  status: "started",
                  kind: "run",
                  pid: 1,
                  argv: [],
                  started_at: "x",
                  log_path: "x",
                  workflow_path: "x",
                  prompt_chars: 0,
                  finished_at: null,
                  exit_code: null,
                },
              }
            : { launch_id: "launch-1", run_id: "r1", status: "started" },
          201,
        );
      }
      if (url.startsWith("/api/schema/run-options"))
        return opts.schema === null ? new Response("{}", { status: 404 }) : jsonResponse(opts.schema);
      if (url.startsWith("/api/workflows")) return jsonResponse(WORKFLOWS);
      if (url.startsWith("/api/templates")) return jsonResponse([TEMPLATE]);
      return new Response("{}", { status: 404 });
    }),
  );
}

describe("launch surfaces use the registry schema", () => {
  it("NewRun renders schema fields (no hand-wired list) and posts typed options", async () => {
    const posts: { url: string; body: any }[] = [];
    stubApi({ schema: SCHEMA, posts });
    render(<NewRun onLaunched={vi.fn()} />);
    await userEvent.selectOptions(await screen.findByLabelText("Turn budget"), "low");
    expect(screen.queryByLabelText("Effort")).toBeNull(); // not in this schema -> not rendered
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    await screen.findByText("Run started");
    expect(posts[0].body.options).toEqual({ turn_budget: "low" });
  });

  it("NewRun falls back to the legacy fields when the schema endpoint is missing", async () => {
    const posts: { url: string; body: any }[] = [];
    stubApi({ schema: null, posts });
    render(<NewRun onLaunched={vi.fn()} />);
    await userEvent.selectOptions(await screen.findByLabelText("Effort"), "high");
    expect(screen.getByLabelText("Max parallel")).toBeInTheDocument();
    expect(screen.queryByLabelText("Turn budget")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    await screen.findByText("Run started");
    expect(posts[0].body.options).toEqual({ effort: "high" });
  });

  it("NewRun blocks submit while an option is invalid", async () => {
    stubApi({ schema: SCHEMA, posts: [] });
    render(<NewRun onLaunched={vi.fn()} />);
    await userEvent.type(await screen.findByLabelText("Max attempts"), "0");
    await waitFor(() => expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled());
  });

  it("TemplateLaunch forwards the chosen options when it starts the run", async () => {
    const posts: { url: string; body: any }[] = [];
    stubApi({ schema: SCHEMA, posts });
    render(<TemplateLaunch onLaunched={vi.fn()} />);
    await userEvent.selectOptions(await screen.findByLabelText("Turn budget"), "high");
    await userEvent.type(screen.getByLabelText("Max attempts"), "4");
    await userEvent.click(screen.getByRole("button", { name: "Create & run" }));
    await screen.findByText("Run started");
    expect(posts[0].url).toBe("/api/templates/t1/instances");
    expect(posts[0].body).toMatchObject({
      start: true,
      options: { turn_budget: "high", max_attempts: 4 },
    });
  });

  it("TemplateLaunch still sends empty options when none are chosen / schema is missing", async () => {
    const posts: { url: string; body: any }[] = [];
    stubApi({ schema: null, posts });
    render(<TemplateLaunch onLaunched={vi.fn()} />);
    await screen.findByLabelText("Max parallel");
    await userEvent.click(screen.getByRole("button", { name: "Create & run" }));
    await screen.findByText("Run started");
    expect(posts[0].body.options).toEqual({});
  });
});
