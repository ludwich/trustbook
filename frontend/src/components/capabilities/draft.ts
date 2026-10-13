import { useCallback, useMemo, useState } from "react";
import type { Capability, CapabilityConfigUpdate, Field, Option, Preset } from "@/api/types";

/**
 * Editable state for one capability form. Keys are field names from `capability.fields`.
 * Toggle fields hold booleans, everything else strings ("" = empty).
 * `api_key` always starts empty: an empty key means "keep the saved key".
 */
export type DraftValues = Record<string, string | boolean>;

export interface Draft {
  values: DraftValues;
  /** User asked to remove the saved API key. */
  clearKey: boolean;
}

/** Fields that only matter when the capability talks to an endpoint. */
const ENDPOINT_FIELDS = new Set(["preset", "base_url", "api_key", "model", "api_version"]);

/** Toggles that are on when the backend has no saved value yet (Phase 3 `code_judge`: default on). */
const TOGGLE_DEFAULTS: Record<string, boolean> = { code_judge: true };

/** Help shown when the manifest sends none for a field. */
const FIELD_HELP: Record<string, string> = {
  code_judge:
    "When the checker can't confirm a sentence about code, the chat model compares it with the " +
    "cited lines. Only those lines and the sentence are sent; the call shows in the request log.",
};

export function fieldHelp(field: Field): string | undefined {
  return field.help ?? FIELD_HELP[field.name];
}

/**
 * Fields that stay visible but can't be changed right now: the code judge only runs as part of
 * answer checking, so it is greyed out while the grounding checker is off.
 */
export function isFieldDisabled(cap: Capability, field: Field, values: DraftValues): boolean {
  return field.name === "code_judge" && hasField(cap, "enabled") && values.enabled === false;
}

export function hasField(cap: Capability, name: string): boolean {
  return cap.fields.some((f) => f.name === name);
}

export function draftFromCapability(cap: Capability): Draft {
  const cfg = cap.config as unknown as Record<string, unknown>;
  const values: DraftValues = {};
  for (const f of cap.fields) {
    if (f.name === "api_key") {
      values[f.name] = "";
    } else if (f.type === "toggle") {
      const v = cfg[f.name];
      values[f.name] =
        v === undefined || v === null ? (TOGGLE_DEFAULTS[f.name] ?? false) : Boolean(v);
    } else {
      const v = cfg[f.name];
      values[f.name] = typeof v === "string" ? v : "";
    }
  }
  return { values, clearKey: false };
}

function str(values: DraftValues, name: string): string {
  const v = values[name];
  return typeof v === "string" ? v : "";
}

export function selectedPreset(cap: Capability, values: DraftValues): Preset | undefined {
  const id = str(values, "preset");
  return cap.presets.find((p) => p.id === id);
}

/** Options for a select field: presets and providers come from the capability itself. */
export function fieldOptions(cap: Capability, field: Field): Option[] {
  if (field.name === "preset" && cap.presets.length) {
    return cap.presets.map((p) => ({ value: p.id, label: p.label }));
  }
  if (field.name === "provider" && cap.providers.length) return cap.providers;
  return field.options ?? [];
}

/**
 * Which fields to show for the current draft. The backend sends every field the capability
 * can have; the UI hides the ones that do not apply to the chosen preset / provider / mode.
 */
export function isFieldVisible(cap: Capability, field: Field, values: DraftValues): boolean {
  const name = field.name;
  // The on/off switch is rendered in the card header, not in the form.
  if (name === "enabled") return false;

  // gate: "use the chat model" hides its own endpoint settings.
  if (hasField(cap, "use_chat") && values.use_chat === true && ENDPOINT_FIELDS.has(name)) {
    return false;
  }
  // stt: endpoint settings only in endpoint mode.
  if (hasField(cap, "mode") && str(values, "mode") !== "endpoint" && ENDPOINT_FIELDS.has(name)) {
    return false;
  }
  // web_search: built-in SearXNG needs no key; hosted providers need no URL.
  if (hasField(cap, "provider")) {
    const provider = str(values, "provider") || "searxng";
    // "mock" (tests only, TRUSTBOOK_MOCK_LLM=1) needs neither a key nor a URL.
    if (name === "api_key") return provider !== "searxng" && provider !== "mock";
    if (name === "base_url") return provider === "searxng";
  }

  const preset = selectedPreset(cap, values);
  if (name === "api_key" && cap.presets.length > 0) {
    // Speech-to-text has no provider field: its endpoint is always OpenAI-compatible.
    if (!hasField(cap, "preset")) return true;
    // Every real endpoint may take a key (optional unless the preset needs one, D-111).
    return preset !== undefined && preset.adapter !== "mock";
  }
  if (name === "api_version") return preset?.adapter === "azure_openai";
  return true;
}

/**
 * True when the visible API key field must have a value: Brave/Tavily, and presets that need
 * one (OpenAI, Azure, Anthropic). Other endpoints take an optional key (OpenRouter, LiteLLM,
 * `llama-server --api-key`); empty means "no key".
 */
export function isKeyRequired(cap: Capability, values: DraftValues): boolean {
  if (hasField(cap, "provider")) {
    const provider = str(values, "provider") || "searxng";
    return provider !== "searxng" && provider !== "mock";
  }
  return selectedPreset(cap, values)?.needs_api_key ?? false;
}

export function visibleFields(cap: Capability, values: DraftValues): Field[] {
  return cap.fields.filter((f) => isFieldVisible(cap, f, values));
}

/** Names of visible fields that still need a value. */
export function missingFields(cap: Capability, draft: Draft): string[] {
  if (hasField(cap, "enabled") && draft.values.enabled === false) return [];
  const missing: string[] = [];
  for (const f of visibleFields(cap, draft.values)) {
    if (f.type === "toggle") continue;
    if (f.name === "api_key") {
      const typed = str(draft.values, "api_key").trim() !== "";
      const saved = cap.config.api_key_set && !draft.clearKey;
      if (!typed && !saved && isKeyRequired(cap, draft.values)) missing.push(f.name);
      continue;
    }
    // Endpoints with presets need preset, URL and model to count as configured (status rule).
    // The test-only "mock" adapter needs neither.
    const isMock = selectedPreset(cap, draft.values)?.adapter === "mock";
    const endpointCore =
      cap.presets.length > 0 &&
      (f.name === "preset" || (!isMock && (f.name === "base_url" || f.name === "model")));
    const needed = f.required || endpointCore;
    if (needed && str(draft.values, f.name).trim() === "") missing.push(f.name);
  }
  return missing;
}

/** Converts the draft into the API update body. */
export function draftToUpdate(cap: Capability, draft: Draft): CapabilityConfigUpdate {
  const out: Record<string, unknown> = {};
  for (const f of cap.fields) {
    const v = draft.values[f.name];
    if (f.name === "api_key") {
      const typed = typeof v === "string" ? v.trim() : "";
      if (typed && isFieldVisible(cap, f, draft.values)) out.api_key = typed;
      else if (draft.clearKey) out.api_key = "";
      continue;
    }
    if (f.type === "toggle") out[f.name] = Boolean(v);
    else out[f.name] = typeof v === "string" && v.trim() !== "" ? v.trim() : null;
  }
  return out as CapabilityConfigUpdate;
}

export function isDraftDirty(cap: Capability, draft: Draft): boolean {
  const initial = draftFromCapability(cap);
  if (draft.clearKey) return true;
  return cap.fields.some((f) => {
    const a = draft.values[f.name];
    const b = initial.values[f.name];
    return typeof a === "string" && typeof b === "string" ? a.trim() !== b.trim() : a !== b;
  });
}

/** New values after picking a preset: prefill its base URL, keep a custom one. */
export function applyPreset(cap: Capability, values: DraftValues, presetId: string): DraftValues {
  const next: DraftValues = { ...values, preset: presetId };
  const preset = cap.presets.find((p) => p.id === presetId);
  const current = str(values, "base_url");
  const isPresetDefault = current === "" || cap.presets.some((p) => p.base_url === current);
  if (preset?.base_url) next.base_url = preset.base_url;
  else if (isPresetDefault) next.base_url = "";
  return next;
}

export interface CapabilityDraftApi {
  draft: Draft;
  setValue: (name: string, value: string | boolean) => void;
  setPreset: (presetId: string) => void;
  setClearKey: (clear: boolean) => void;
  reset: (cap?: Capability) => void;
  dirty: boolean;
  update: CapabilityConfigUpdate;
  missing: string[];
}

export function useCapabilityDraft(cap: Capability): CapabilityDraftApi {
  const [draft, setDraft] = useState<Draft>(() => draftFromCapability(cap));
  const [touched, setTouched] = useState(false);

  // When the saved config changes underneath an untouched form, follow it.
  const signature = JSON.stringify([cap.config, cap.fields.map((f) => f.name)]);
  const [seen, setSeen] = useState(signature);
  if (seen !== signature) {
    setSeen(signature);
    if (!touched) setDraft(draftFromCapability(cap));
  }

  const setValue = useCallback((name: string, value: string | boolean) => {
    setTouched(true);
    setDraft((d) => ({ ...d, values: { ...d.values, [name]: value } }));
  }, []);

  const setPreset = useCallback(
    (presetId: string) => {
      setTouched(true);
      setDraft((d) => ({ ...d, values: applyPreset(cap, d.values, presetId) }));
    },
    [cap],
  );

  const setClearKey = useCallback((clear: boolean) => {
    setTouched(true);
    setDraft((d) => ({
      values: clear ? { ...d.values, api_key: "" } : d.values,
      clearKey: clear,
    }));
  }, []);

  const reset = useCallback(
    (next?: Capability) => {
      setTouched(false);
      setDraft(draftFromCapability(next ?? cap));
    },
    [cap],
  );

  const dirty = useMemo(() => isDraftDirty(cap, draft), [cap, draft]);
  const update = useMemo(() => draftToUpdate(cap, draft), [cap, draft]);
  const missing = useMemo(() => missingFields(cap, draft), [cap, draft]);

  return { draft, setValue, setPreset, setClearKey, reset, dirty, update, missing };
}
