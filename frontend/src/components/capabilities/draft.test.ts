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
  isKeyRequired,
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

  it("shows an API key for every endpoint preset, API version only for Azure", () => {
    const d = draftFromCapability(chat);
    expect(names(visibleFields(chat, d.values))).toEqual(["preset", "base_url", "model"]);
    expect(names(visibleFields(chat, withValues(d, { preset: "ollama" }).values))).toEqual([
      "preset",
      "base_url",
      "api_key",
      "model",
    ]);
    expect(names(visibleFields(chat, withValues(d, { preset: "custom" }).values))).toEqual([
      "preset",
      "base_url",
      "api_key",
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
      "base_url",
      "api_key",
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

  it("an optional key (G3b-2): not required, sent when typed, kept or cleared like a preset key", () => {
    const empty = withValues(draftFromCapability(chatCapability()), {
      preset: "custom",
      base_url: "https://openrouter.example/api/v1",
      model: "m",
    });
    expect(isKeyRequired(chatCapability(), empty.values)).toBe(false);
    expect(missingFields(chatCapability(), empty)).toEqual([]);
    expect(draftToUpdate(chatCapability(), empty)).not.toHaveProperty("api_key");
    expect(draftToUpdate(chatCapability(), withValues(empty, { api_key: " or-k " })).api_key).toBe(
      "or-k",
    );

    const saved = chatCapability({
      config: config({
        preset: "custom",
        base_url: "https://openrouter.example/api/v1",
        model: "m",
        api_key_set: true,
        api_key_hint: "…7d6c",
      }),
    });
    const d = withValues(draftFromCapability(saved), { model: "m2" });
    expect(draftToUpdate(saved, d)).not.toHaveProperty("api_key"); // other edits keep the key
    const cleared: Draft = { ...d, clearKey: true };
    expect(draftToUpdate(saved, cleared).api_key).toBe("");
    expect(missingFields(saved, cleared)).toEqual([]);

    const stt = sttCapability();
    const sttDraft = withValues(draftFromCapability(stt), {
      mode: "endpoint",
      base_url: "http://stt:9000/v1",
      model: "whisper-1",
      api_key: "stt-k",
    });
    expect(isKeyRequired(stt, sttDraft.values)).toBe(false);
    expect(draftToUpdate(stt, sttDraft).api_key).toBe("stt-k");
  });

  it("still requires a key for presets that need one", () => {
    const chat = chatCapability();
    const d = withValues(draftFromCapability(chat), {
      preset: "openai",
      base_url: "https://api.openai.com/v1",
      model: "gpt-x",
    });
    expect(isKeyRequired(chat, d.values)).toBe(true);
    expect(missingFields(chat, d)).toEqual(["api_key"]);
  });

  it("requires preset, URL and model for endpoints, empty strings become null", () => {
    const chat = chatCapability();
    const d = withValues(draftFromCapability(chat), { preset: "custom" });
    expect(missingFields(chat, d)).toEqual(["base_url", "model"]);
    expect(draftToUpdate(chat, d)).toMatchObject({ preset: "custom", base_url: null, model: null });
  });
});
