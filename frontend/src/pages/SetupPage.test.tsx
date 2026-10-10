import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { CapabilityConfigUpdate, TestResult } from "@/api/types";
import { allCapabilities, chatCapability, config } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

function result(ok: boolean, message: string): TestResult {
  return { ok, message, latency_ms: ok ? 412 : null, tested_at: new Date().toISOString() };
}

let completed: boolean;
let testReply: TestResult;
let tests: CapabilityConfigUpdate[];
let puts: Record<string, CapabilityConfigUpdate[]>;
let completeCalls: number;

beforeEach(() => {
  completed = false;
  testReply = result(true, "The model replied.");
  tests = [];
  puts = {};
  completeCalls = 0;
  server.use(
    http.get("*/api/setup", () => HttpResponse.json({ completed, chat_ready: false })),
    http.post("*/api/capabilities/chat/test", async ({ request }) => {
      tests.push((await request.json()) as CapabilityConfigUpdate);
      return HttpResponse.json(testReply);
    }),
    http.put("*/api/capabilities/:id", async ({ request, params }) => {
      const id = String(params.id);
      const body = (await request.json()) as CapabilityConfigUpdate;
      (puts[id] ??= []).push(body);
      const cap = allCapabilities().find((c) => c.id === id)!;
      return HttpResponse.json({ ...cap, status: "ready" });
    }),
    http.post("*/api/setup/complete", () => {
      completeCalls += 1;
      completed = true;
      return HttpResponse.json({ completed: true, chat_ready: true });
    }),
  );
});

async function fillChat(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(await screen.findByLabelText("Provider"), "llama_server");
  expect(screen.getByLabelText("Base URL")).toHaveValue("http://host.docker.internal:8080/v1");
  await user.type(screen.getByLabelText("Model"), "qwen3-14b");
}

describe("setup wizard", () => {
  it("redirects to /setup on first start", async () => {
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Set up Trustbook" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/setup");
  });

  it("requires a successful test of the current draft before saving, then completes", async () => {
    const user = userEvent.setup();
    renderApp("/setup");
    await fillChat(user);

    const saveBtn = screen.getByRole("button", { name: /save and continue/i });
    expect(saveBtn).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save anyway" })).not.toBeInTheDocument();

    // A failing test offers "Save anyway" but keeps the normal save disabled.
    testReply = result(false, "Connection refused.");
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Connection refused\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save anyway" })).toBeInTheDocument();
    expect(saveBtn).toBeDisabled();
    // The draft (not the saved config) was tested, without an API key for llama-server.
    expect(tests[0]).toMatchObject({
      preset: "llama_server",
      base_url: "http://host.docker.internal:8080/v1",
      model: "qwen3-14b",
    });
    expect(tests[0]).not.toHaveProperty("api_key");

    // A passing test enables saving.
    testReply = result(true, "The model replied.");
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/The model replied\./)).toHaveTextContent("(412 ms)");
    expect(saveBtn).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Save anyway" })).not.toBeInTheDocument();

    // Editing after the test makes it stale again.
    await user.type(screen.getByLabelText("Model"), "-q4");
    expect(screen.getByText(/changed the settings after the last test/i)).toBeInTheDocument();
    expect(saveBtn).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(saveBtn).toBeEnabled());

    await user.click(saveBtn);
    expect(await screen.findByRole("heading", { name: "Optional extras" })).toBeInTheDocument();
    expect(puts.chat).toEqual([
      expect.objectContaining({ preset: "llama_server", model: "qwen3-14b-q4" }),
    ]);

    // Step 2 defaults to the built-in options; skipping saves nothing.
    expect(screen.getByRole("radio", { name: "Use built-in speech-to-text" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Built-in search (SearXNG)" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(puts.stt).toBeUndefined();

    // Step 3 → complete → spaces list.
    expect(await screen.findByRole("heading", { name: "You're ready" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /go to spaces/i }));
    expect(await screen.findByRole("heading", { name: "Spaces" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
    expect(completeCalls).toBe(1);
  });

  it("lets the user save anyway after a failed test", async () => {
    const user = userEvent.setup();
    renderApp("/setup");
    await fillChat(user);
    testReply = result(false, "Timed out after 30 s.");
    await user.click(screen.getByRole("button", { name: "Test" }));
    await user.click(await screen.findByRole("button", { name: "Save anyway" }));
    expect(await screen.findByRole("heading", { name: "Optional extras" })).toBeInTheDocument();
    expect(puts.chat).toHaveLength(1);
  });

  it("blocks Test until required fields are filled and asks for a key when the preset needs one", async () => {
    const user = userEvent.setup();
    renderApp("/setup");
    await user.selectOptions(await screen.findByLabelText("Provider"), "openai");
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(screen.getAllByText("Required")).toHaveLength(2); // API key + model
    expect(tests).toHaveLength(0);

    await user.type(screen.getByLabelText("API key"), "sk-test");
    await user.type(screen.getByLabelText("Model"), "gpt-x");
    await user.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(tests).toHaveLength(1));
    expect(tests[0]).toMatchObject({ preset: "openai", api_key: "sk-test" });
    // Remote endpoints say where data goes.
    expect(screen.getByText(/sent to/)).toHaveTextContent("api.openai.com");
  });

  it("loads model suggestions from the endpoint", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/capabilities/chat/models", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ models: ["qwen3-14b", "llama3.1-8b"] });
      }),
    );
    const { container } = renderApp("/setup");
    await user.selectOptions(await screen.findByLabelText("Provider"), "ollama");
    await user.click(screen.getByRole("button", { name: /load models/i }));
    expect(await screen.findByText(/2 models found/)).toBeInTheDocument();
    const options = container.querySelectorAll("datalist option");
    expect([...options].map((o) => o.getAttribute("value"))).toEqual(["qwen3-14b", "llama3.1-8b"]);
    expect(bodies[0]).toMatchObject({
      preset: "ollama",
      base_url: "http://host.docker.internal:11434/v1",
    });
  });

  it("shows a model-listing error in plain English", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("*/api/capabilities/chat/models", () =>
        HttpResponse.json(
          { detail: "Could not connect to host.docker.internal:11434." },
          { status: 502 },
        ),
      ),
    );
    renderApp("/setup");
    await user.selectOptions(await screen.findByLabelText("Provider"), "ollama");
    await user.click(screen.getByRole("button", { name: /load models/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not connect to host.docker.internal:11434.",
    );
  });

  it("saves optional extras on Continue and validates a missing search key", async () => {
    const user = userEvent.setup();
    const ready = chatCapability({
      status: "ready",
      locality: "local",
      config: config({
        preset: "ollama",
        base_url: "http://host.docker.internal:11434/v1",
        model: "qwen3",
      }),
    });
    server.use(
      http.get("*/api/capabilities", () =>
        HttpResponse.json(allCapabilities().map((c) => (c.id === "chat" ? ready : c))),
      ),
    );
    renderApp("/setup");
    // A working, unchanged chat config can continue without re-testing.
    await user.click(await screen.findByRole("button", { name: /^continue/i }));
    const step = await screen.findByRole("heading", { name: "Optional extras" });
    const card = step.closest("section")!;

    await user.click(within(card).getByRole("radio", { name: "Brave Search" }));
    await user.click(within(card).getByRole("button", { name: /^continue/i }));
    expect(within(card).getByText("Required")).toBeInTheDocument();
    expect(puts.web_search).toBeUndefined();

    await user.type(within(card).getByLabelText("API key"), "brave-key");
    await user.click(within(card).getByRole("button", { name: /^continue/i }));
    expect(await screen.findByRole("heading", { name: "You're ready" })).toBeInTheDocument();
    expect(puts.web_search).toEqual([{ provider: "brave", api_key: "brave-key" }]);
    expect(puts.stt).toBeUndefined();
  });
});
