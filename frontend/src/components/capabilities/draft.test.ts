import { describe, expect, it } from "vitest";
import {
  chatCapability,
  config,
  gateCapability,
  searchCapability,
  sttCapability,
} from "@/test/fixtures";
import {
  applyPreset,
  draftFromCapability,
  draftToUpdate,
  isDraftDirty,
  missingFields,
  visibleFields,
  type Draft,
} from "./draft";

const names = (fs: { name: string }[]) => fs.map((f) => f.name);

function withValues(d: Draft, values: Record<string, string | boolean>): Draft {
  return { ...d, values: { ...d.values, ...values } };
}

describe("field visibility", () => {
  const chat = chatCapability();

  it("shows API key only for presets that need one, API version only for Azure", () => {
    const d = draftFromCapability(chat);
    expect(names(visibleFields(chat, withValues(d, { preset: "ollama" }).values))).toEqual([
      "preset",
      "base_url",
      "model",
    ]);
    expect(names(visibleFields(chat, withValues(d, { preset: "openai" }).values))).toEqual([
      "preset",
      "base_url",
      "api_key",
      "model",
    ]);
    expect(names(visibleFields(chat, withValues(d, { preset: "azure_openai" }).values))).toEqual([
      "preset",
      "base_url",
      "api_key",
      "model",
      "api_version",
    ]);
  });

  it("hides gate endpoint fields while it reuses the chat model", () => {
    const gate = gateCapability();
    const d = draftFromCapability(gate);
    expect(names(visibleFields(gate, d.values))).toEqual(["use_chat"]);
    expect(
      names(visibleFields(gate, withValues(d, { use_chat: false, preset: "ollama" }).values)),
    ).toContain("base_url");
  });

  it("shows STT endpoint fields only in endpoint mode", () => {
    const stt = sttCapability();
    const d = draftFromCapability(stt);
    expect(names(visibleFields(stt, d.values))).toEqual(["mode"]);
    expect(names(visibleFields(stt, withValues(d, { mode: "endpoint" }).values))).toEqual([
      "mode",
      "preset",
      "base_url",
      "model",
    ]);
  });

  it("asks for a search key only for Brave and Tavily", () => {
    const s = searchCapability();
    const d = draftFromCapability(s);
    expect(names(visibleFields(s, d.values))).toEqual(["provider"]);
    expect(names(visibleFields(s, withValues(d, { provider: "brave" }).values))).toEqual([
      "provider",
      "api_key",
    ]);
    // The test-only mock provider has fixed results: no key, no URL.
    expect(names(visibleFields(s, withValues(d, { provider: "mock" }).values))).toEqual([
      "provider",
    ]);
  });
});

describe("draft to update", () => {
  it("prefills the preset URL but keeps a custom one", () => {
    const chat = chatCapability();
    const d = draftFromCapability(chat);
    const a = applyPreset(chat, d.values, "ollama");
    expect(a.base_url).toBe("http://host.docker.internal:11434/v1");
    const b = applyPreset(chat, a, "custom");
    expect(b.base_url).toBe(""); // was a preset default → cleared for the user to type
    const c = applyPreset(chat, { ...b, base_url: "http://my-box:9000/v1" }, "custom");
    expect(c.base_url).toBe("http://my-box:9000/v1");
  });

  it("omits an empty API key (keep saved) and sends '' to clear it", () => {
    const chat = chatCapability({
      config: config({
        preset: "openai",
        base_url: "https://api.openai.com/v1",
        model: "gpt-x",
        api_key_set: true,
        api_key_hint: "…a1b2",
      }),
    });
    const d = draftFromCapability(chat);
    expect(isDraftDirty(chat, d)).toBe(false);
    expect(draftToUpdate(chat, d)).not.toHaveProperty("api_key");
    expect(missingFields(chat, d)).toEqual([]);

    const typed = withValues(d, { api_key: " sk-new " });
    expect(draftToUpdate(chat, typed).api_key).toBe("sk-new");

    const cleared: Draft = { ...d, clearKey: true };
    expect(draftToUpdate(chat, cleared).api_key).toBe("");
    expect(missingFields(chat, cleared)).toEqual(["api_key"]);
  });

  it("requires preset, URL and model for endpoints, empty strings become null", () => {
    const chat = chatCapability();
    const d = withValues(draftFromCapability(chat), { preset: "custom" });
    expect(missingFields(chat, d)).toEqual(["base_url", "model"]);
    expect(draftToUpdate(chat, d)).toMatchObject({ preset: "custom", base_url: null, model: null });
  });
});
