import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  TemplateLaunch,
  buildCreateInstanceRequest,
  missingRequiredParams,
} from "../components/TemplateLaunch";
import type { CreateInstanceResponse, TemplateInfo, TemplateParam } from "../types";

const REQUIRED_PARAM: TemplateParam = {
  name: "repo_set",
  description: "Reposet to operate on",
  required: true,
  enum: null,
  default: null,
};

const ENUM_PARAM: TemplateParam = {
  name: "type",
  description: "Force the route (omit to auto-classify)",
  required: false,
  enum: ["bug", "epic", "task", "documentation", "testing"],
  default: null,
};

const TEMPLATES: TemplateInfo[] = [
  {
    name: "routed-runner",
    description: "One prompt -> classified route -> done",
    path: "/pkg/templates/routed-runner",
    source: "builtin",
    params: [REQUIRED_PARAM, ENUM_PARAM],
    required_agents: ["architect", "developer", "tester", "reviewer", "manager"],
    prompt_skeleton: "# Project context\n\nDescribe the change…\n",
  },
  {
    name: "no-params",
    description: "A template with no declared params",
    path: "/pkg/templates/no-params",
    source: "workspace",
    params: [],
    required_agents: [],
    prompt_skeleton: null,
  },
];

/** Route GET by URL prefix; POST always goes through `onPost`, which picks the response. */
function stubFetch(
  getRoutes: Record<string, unknown>,
  onPost: (url: string, body: unknown) => { status: number; body: unknown },
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const { status, body } = onPost(url, JSON.parse(String(init.body ?? "{}")));
        return new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      }
      const key = Object.keys(getRoutes).find((route) => url.startsWith(route));
      if (!key) return new Response("{}", { status: 404 });
      return new Response(JSON.stringify(getRoutes[key]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("missingRequiredParams", () => {
  it("flags a required param with no value", () => {
    expect(missingRequiredParams([REQUIRED_PARAM], {})).toEqual([REQUIRED_PARAM]);
  });

  it("flags a required param whose value is whitespace-only", () => {
    expect(missingRequiredParams([REQUIRED_PARAM], { repo_set: "   " })).toEqual([
      REQUIRED_PARAM,
    ]);
  });

  it("does not flag an optional param", () => {
    expect(missingRequiredParams([ENUM_PARAM], {})).toEqual([]);
  });

  it("clears once a non-empty value is set", () => {
    expect(missingRequiredParams([REQUIRED_PARAM], { repo_set: "main" })).toEqual([]);
  });
});

describe("buildCreateInstanceRequest", () => {
  it("omits blank param values so server defaults apply", () => {
    const body = buildCreateInstanceRequest({
      slug: "",
      paramValues: { repo_set: "main", type: "" },
      prompt: "do the thing",
      start: false,
    });
    expect(body.params).toEqual({ repo_set: "main" });
  });

  it("omits slug_or_id when the slug is blank/whitespace", () => {
    const body = buildCreateInstanceRequest({
      slug: "   ",
      paramValues: {},
      prompt: "",
      start: false,
    });
    expect(body.slug_or_id).toBeUndefined();
  });

  it("trims and includes a non-blank slug", () => {
    const body = buildCreateInstanceRequest({
      slug: "  my-slug  ",
      paramValues: {},
      prompt: "",
      start: true,
    });
    expect(body.slug_or_id).toBe("my-slug");
  });

  it("carries the start flag and prompt through verbatim", () => {
    const body = buildCreateInstanceRequest({
      slug: "",
      paramValues: {},
      prompt: "hello",
      start: true,
    });
    expect(body.start).toBe(true);
    expect(body.prompt).toBe("hello");
  });
});

describe("TemplateLaunch", () => {
  it("shows an empty-state explaining how templates are registered when none exist", async () => {
    stubFetch({ "/api/templates": [] }, () => {
      throw new Error("unexpected POST");
    });
    render(<TemplateLaunch onLaunched={() => {}} />);

    expect(await screen.findByText(/No templates registered/)).toBeInTheDocument();
    expect(screen.getByText("templates:")).toBeInTheDocument();
  });

  it("lists templates by name + description and prefills the prompt with the skeleton", async () => {
    stubFetch({ "/api/templates": TEMPLATES }, () => {
      throw new Error("unexpected POST");
    });
    render(<TemplateLaunch onLaunched={() => {}} />);

    const select = await screen.findByLabelText("Template");
    expect(within(select).getByText(/routed-runner — One prompt/)).toBeInTheDocument();

    const prompt = (await screen.findByLabelText("Prompt")) as HTMLTextAreaElement;
    await waitFor(() => expect(prompt.value).toContain("Project context"));
  });

  it("renders an enum param as a select and a plain param as a text input", async () => {
    stubFetch({ "/api/templates": TEMPLATES }, () => {
      throw new Error("unexpected POST");
    });
    render(<TemplateLaunch onLaunched={() => {}} />);

    await screen.findByLabelText("Template");
    expect((await screen.findByLabelText("type")).tagName).toBe("SELECT");
    const typeSelect = (await screen.findByLabelText("type")) as HTMLSelectElement;
    const optionValues = Array.from(typeSelect.options).map((o) => o.value);
    expect(optionValues).toEqual(expect.arrayContaining(ENUM_PARAM.enum as string[]));

    expect((await screen.findByLabelText(/repo_set/)).tagName).toBe("INPUT");
  });

  it("blocks Create client-side when a required param is missing, without calling the API", async () => {
    const posted: unknown[] = [];
    stubFetch({ "/api/templates": TEMPLATES }, (_url, body) => {
      posted.push(body);
      return { status: 201, body: {} };
    });
    render(<TemplateLaunch onLaunched={() => {}} />);

    await screen.findByLabelText("Template");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/Missing required parameter/);
    expect(banner).toHaveTextContent("repo_set");
    expect(posted).toHaveLength(0);
  });

  it("Create (start:false) posts start:false and shows the created instance paths", async () => {
    const posted: { url: string; body: Record<string, unknown> }[] = [];
    const response: CreateInstanceResponse = {
      instance_dir: "/ws/workflows/routed-runner/runs/e-abc123-fix-bug",
      workflow_path: "/ws/workflows/routed-runner/runs/e-abc123-fix-bug/workflow.json",
      workflow: {
        id: "e-abc123-fix-bug",
        name: "e-abc123-fix-bug",
        path: "/ws/workflows/routed-runner/runs/e-abc123-fix-bug/workflow.json",
        task_count: 5,
        prompt_path: "prompt.md",
        general_instructions: [],
        error: null,
      },
      launch: null,
    };
    stubFetch({ "/api/templates": TEMPLATES }, (url, body) => {
      posted.push({ url, body: body as Record<string, unknown> });
      return { status: 201, body: response };
    });

    const onLaunched = vi.fn();
    render(<TemplateLaunch onLaunched={onLaunched} />);

    await userEvent.type(await screen.findByLabelText(/repo_set/), "main");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].url).toBe("/api/templates/routed-runner/instances");
    expect(posted[0].body).toMatchObject({ start: false, params: { repo_set: "main" } });

    const banner = (await screen.findByText(/e-abc123-fix-bug\/workflow.json/)).closest(
      ".banner",
    )!;
    expect(within(banner as HTMLElement).getByText("From workflow")).toBeInTheDocument();
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("Create & run (start:true) posts start:true and launches using launch.run_id", async () => {
    const posted: Record<string, unknown>[] = [];
    const response: CreateInstanceResponse = {
      instance_dir: "/ws/workflows/routed-runner/runs/e-abc123-fix-bug",
      workflow_path: "/ws/workflows/routed-runner/runs/e-abc123-fix-bug/workflow.json",
      workflow: {
        id: "e-abc123-fix-bug",
        name: "e-abc123-fix-bug",
        path: "/ws/workflows/routed-runner/runs/e-abc123-fix-bug/workflow.json",
        task_count: 5,
        prompt_path: "prompt.md",
        general_instructions: [],
        error: null,
      },
      launch: {
        launch_id: "launch-9",
        kind: "run",
        pid: 4242,
        argv: ["ao", "run"],
        started_at: "2026-07-24T10:00:00+00:00",
        log_path: "/ws/.orchestrator/launch-9.log",
        run_id: "e-abc123-fix-bug-20260724T100000Z",
        workflow_path: "/ws/workflows/routed-runner/runs/e-abc123-fix-bug/workflow.json",
        prompt_chars: 42,
        finished_at: null,
        exit_code: null,
        cancelled: false,
      },
    };
    stubFetch({ "/api/templates": TEMPLATES }, (_url, body) => {
      posted.push(body as Record<string, unknown>);
      return { status: 201, body: response };
    });

    const onLaunched = vi.fn();
    render(<TemplateLaunch onLaunched={onLaunched} />);

    await userEvent.type(await screen.findByLabelText(/repo_set/), "main");
    await userEvent.click(screen.getByRole("button", { name: "Create & run" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ start: true });
    await waitFor(() =>
      expect(onLaunched).toHaveBeenCalledWith("e-abc123-fix-bug-20260724T100000Z"),
    );
  });

  it("shows the server error detail (e.g. a 409 prompt conflict) via the banner", async () => {
    stubFetch({ "/api/templates": TEMPLATES }, () => ({
      status: 409,
      body: { detail: "instance exists with a conflicting prompt" },
    }));

    render(<TemplateLaunch onLaunched={() => {}} />);
    await userEvent.type(await screen.findByLabelText(/repo_set/), "main");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText("instance exists with a conflicting prompt"),
    ).toBeInTheDocument();
  });

  it("resets param values and prompt when the selected template changes", async () => {
    stubFetch({ "/api/templates": TEMPLATES }, () => {
      throw new Error("unexpected POST");
    });
    render(<TemplateLaunch onLaunched={() => {}} />);

    const repoSet = await screen.findByLabelText(/repo_set/);
    await userEvent.type(repoSet, "main");

    const select = screen.getByLabelText("Template");
    await userEvent.selectOptions(select, "no-params");

    // The no-params template declares no params and no skeleton.
    expect(screen.queryByLabelText(/repo_set/)).not.toBeInTheDocument();
    const prompt = (await screen.findByLabelText("Prompt")) as HTMLTextAreaElement;
    expect(prompt.value).toBe("");
  });
});
