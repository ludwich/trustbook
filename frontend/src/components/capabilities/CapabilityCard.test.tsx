import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { CapabilityConfigUpdate } from "@/api/types";
import {
  chatCapability,
  config,
  embeddingsCapability,
  gateCapability,
  groundingCapability,
  searchCapability,
} from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { CapabilityCard } from "./CapabilityCard";

describe("CapabilityCard", () => {
  it("shows label, required tag, status pill and no locality when not configured", () => {
    renderWithProviders(<CapabilityCard capability={chatCapability()} />);
    const card = screen.getByTestId("capability-card-chat");
    expect(within(card).getByRole("heading", { name: "Chat model" })).toBeInTheDocument();
    expect(within(card).getByText("Required")).toBeInTheDocument();
    expect(within(card).getByText("Not set up")).toBeInTheDocument();
    expect(within(card).getByText("Choose a provider and model.")).toBeInTheDocument();
    expect(within(card).queryByText("Local")).not.toBeInTheDocument();
    expect(within(card).queryByText("Remote")).not.toBeInTheDocument();
  });

  it("shows a Remote badge for a remote endpoint and a Local badge for bundled models", () => {
    renderWithProviders(
      <>
        <CapabilityCard
          capability={chatCapability({
            status: "ready",
            locality: "remote",
            config: config({
              preset: "openai",
              base_url: "https://api.openai.com/v1",
              model: "gpt-x",
              api_key_set: true,
            }),
          })}
        />
        <CapabilityCard capability={embeddingsCapability()} />
      </>,
    );
    const chat = screen.getByTestId("capability-card-chat");
    expect(within(chat).getByText("Ready")).toBeInTheDocument();
    expect(within(chat).getByText("Remote")).toHaveAttribute(
      "title",
      "Text is sent to api.openai.com.",
    );

    const emb = screen.getByTestId("capability-card-embeddings");
    expect(within(emb).getByText("Local")).toBeInTheDocument();
    expect(within(emb).getByTestId("bundled-model")).toHaveTextContent("google/embeddinggemma-2");
    expect(within(emb).getByText("Apache-2.0")).toBeInTheDocument();
    // Bundled-only capabilities have nothing to save, only a Test.
    expect(within(emb).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(within(emb).getByRole("button", { name: /test/i })).toBeInTheDocument();
  });

  it("shows error and disabled states", () => {
    renderWithProviders(
      <>
        <CapabilityCard
          capability={embeddingsCapability({
            status: "error",
            status_detail: "Model files are missing.",
            bundled: {
              model_id: "google/embeddinggemma-2",
              revision: "x",
              license: "Apache-2.0",
              present: false,
              fallback: false,
            },
          })}
        />
        <CapabilityCard
          capability={groundingCapability({
            status: "disabled",
            config: config({ enabled: false }),
          })}
        />
      </>,
    );
    const emb = screen.getByTestId("capability-card-embeddings");
    expect(within(emb).getByText("Error")).toBeInTheDocument();
    expect(within(emb).getByText(/model files are missing from this install/i)).toBeInTheDocument();
    const g = screen.getByTestId("capability-card-grounding");
    expect(within(g).getByText("Turned off")).toBeInTheDocument();
    expect(within(g).getByText(/marked "Unverified"/)).toBeInTheDocument();
  });

  it("changes which fields show when the preset changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CapabilityCard capability={chatCapability()} />);
    const card = screen.getByTestId("capability-card-chat");

    await user.selectOptions(within(card).getByLabelText("Provider"), "ollama");
    expect(within(card).getByLabelText("Base URL")).toHaveValue(
      "http://host.docker.internal:11434/v1",
    );
    // Local servers and "Other OpenAI-compatible" take an optional key (G3b-2).
    expect(within(card).getByLabelText("API key (optional)")).toBeInTheDocument();
    expect(within(card).queryByLabelText("API version")).not.toBeInTheDocument();

    await user.selectOptions(within(card).getByLabelText("Provider"), "azure_openai");
    expect(within(card).getByLabelText("API key")).toBeInTheDocument();
    expect(within(card).queryByLabelText("API key (optional)")).not.toBeInTheDocument();
    expect(within(card).getByLabelText("API version")).toBeInTheDocument();
  });

  it("gate hides endpoint fields while 'Use the chat model' is on", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CapabilityCard capability={gateCapability()} />);
    const card = screen.getByTestId("capability-card-gate");
    expect(within(card).queryByLabelText("Base URL")).not.toBeInTheDocument();
    await user.click(within(card).getByRole("switch", { name: "Use the chat model" }));
    expect(within(card).getByLabelText("Provider")).toBeInTheDocument();
  });

  it("web search shows a key field only for Brave/Tavily", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CapabilityCard capability={searchCapability()} />);
    const card = screen.getByTestId("capability-card-web_search");
    expect(within(card).queryByLabelText("API key")).not.toBeInTheDocument();
    await user.click(within(card).getByRole("radio", { name: "Brave" }));
    expect(within(card).getByLabelText("API key")).toBeInTheDocument();
  });

  it("saves the draft and tests the saved config when nothing changed", async () => {
    const user = userEvent.setup();
    const puts: CapabilityConfigUpdate[] = [];
    const tests: unknown[] = [];
    const saved = chatCapability({
      status: "ready",
      locality: "local",
      config: config({
        preset: "ollama",
        base_url: "http://host.docker.internal:11434/v1",
        model: "qwen3",
      }),
    });
    server.use(
      http.put("*/api/capabilities/chat", async ({ request }) => {
        puts.push((await request.json()) as CapabilityConfigUpdate);
        return HttpResponse.json(saved);
      }),
      http.post("*/api/capabilities/chat/test", async ({ request }) => {
        tests.push(await request.json());
        return HttpResponse.json({
          ok: true,
          message: "Replied OK",
          latency_ms: 840,
          tested_at: new Date().toISOString(),
        });
      }),
    );
    renderWithProviders(<CapabilityCard capability={saved} />);
    const card = screen.getByTestId("capability-card-chat");

    const save = within(card).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await user.click(within(card).getByRole("button", { name: /test/i }));
    expect(await within(card).findByText(/Replied OK/)).toHaveTextContent("(840 ms)");
    expect(tests).toEqual([{}]);

    const model = within(card).getByLabelText("Model");
    await user.clear(model);
    await user.type(model, "llama3");
    expect(save).toBeEnabled();
    await user.click(save);
    expect(puts).toHaveLength(1);
    expect(puts[0]).toMatchObject({ preset: "ollama", model: "llama3" });
    expect(puts[0]).not.toHaveProperty("api_key");
  });
});
