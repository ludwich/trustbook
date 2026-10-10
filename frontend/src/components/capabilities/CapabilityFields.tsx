import { IconListSearch } from "@tabler/icons-react";
import type { Capability, Field, Option } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  fieldHelp,
  fieldOptions,
  isFieldDisabled,
  visibleFields,
  type CapabilityDraftApi,
} from "./draft";

export interface ModelSuggestions {
  models: string[];
  loading: boolean;
  /** Plain-English result line, e.g. "12 models found" or an error. */
  message: string | null;
  error: boolean;
  onLoad: () => void;
}

interface Props {
  capability: Capability;
  form: CapabilityDraftApi;
  /** Prefix for element ids; must be unique on the page. */
  idPrefix: string;
  /** Show "Required" hints for empty required fields. */
  showErrors?: boolean;
  /** Override option labels per field, e.g. { mode: { bundled: "Use built-in…" } }. */
  optionLabels?: Record<string, Record<string, string>>;
  models?: ModelSuggestions;
  disabled?: boolean;
}

/** Renders the editable fields of a capability for the current draft. */
export function CapabilityFields({
  capability,
  form,
  idPrefix,
  showErrors = false,
  optionLabels,
  models,
  disabled = false,
}: Props) {
  const fields = visibleFields(capability, form.draft.values);
  if (fields.length === 0) return null;

  return (
    <div className="grid gap-3.5">
      {fields.map((field) => (
        <FieldRow
          key={field.name}
          field={field}
          capability={capability}
          form={form}
          id={`${idPrefix}-${field.name}`}
          invalid={showErrors && form.missing.includes(field.name)}
          optionLabels={optionLabels?.[field.name]}
          models={field.name === "model" ? models : undefined}
          disabled={disabled || isFieldDisabled(capability, field, form.draft.values)}
        />
      ))}
    </div>
  );
}

function FieldRow({
  field,
  capability,
  form,
  id,
  invalid,
  optionLabels,
  models,
  disabled,
}: {
  field: Field;
  capability: Capability;
  form: CapabilityDraftApi;
  id: string;
  invalid: boolean;
  optionLabels?: Record<string, string>;
  models?: ModelSuggestions;
  disabled: boolean;
}) {
  const value = form.draft.values[field.name];
  const helpText = fieldHelp(field);
  const helpId = helpText ? `${id}-help` : undefined;
  const errorId = invalid ? `${id}-error` : undefined;
  const describedBy = [helpId, errorId].filter(Boolean).join(" ") || undefined;

  const help = helpText ? (
    <p id={helpId} className="text-xs text-muted-foreground">
      {helpText}
    </p>
  ) : null;
  const error = invalid ? (
    <p id={errorId} className="text-xs font-medium text-destructive">
      Required
    </p>
  ) : null;

  if (field.type === "toggle") {
    return (
      <div className="flex items-start gap-3">
        <Switch
          id={id}
          checked={value === true}
          onCheckedChange={(v) => form.setValue(field.name, v)}
          aria-describedby={describedBy}
          disabled={disabled}
          className="mt-0.5"
        />
        <div className="grid gap-1">
          <Label htmlFor={id}>{field.label}</Label>
          {help}
        </div>
      </div>
    );
  }

  if (field.type === "select") {
    const options: Option[] = fieldOptions(capability, field).map((o) => ({
      value: o.value,
      label: optionLabels?.[o.value] ?? o.label,
    }));
    const current = typeof value === "string" ? value : "";
    const onChange = (v: string) =>
      field.name === "preset" ? form.setPreset(v) : form.setValue(field.name, v);

    // Few choices read better as a segmented radio group.
    if (field.name !== "preset" && options.length > 0 && options.length <= 3) {
      return (
        <fieldset className="grid gap-1.5" aria-describedby={describedBy}>
          <legend className="mb-1.5 text-sm font-medium">{field.label}</legend>
          <div role="radiogroup" className="flex flex-wrap gap-1.5">
            {options.map((o) => {
              const checked = current === o.value;
              return (
                <label
                  key={o.value}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors",
                    "has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50",
                    checked
                      ? "border-primary bg-info-soft text-foreground"
                      : "border-input bg-card hover:bg-accent",
                    disabled && "pointer-events-none opacity-50",
                  )}
                >
                  <input
                    type="radio"
                    name={id}
                    value={o.value}
                    checked={checked}
                    disabled={disabled}
                    onChange={() => onChange(o.value)}
                    className="accent-[var(--primary)]"
                  />
                  {o.label}
                </label>
              );
            })}
          </div>
          {help}
          {error}
        </fieldset>
      );
    }

    return (
      <div className="grid gap-1.5">
        <Label htmlFor={id}>{field.label}</Label>
        <NativeSelect
          id={id}
          value={current}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          disabled={disabled}
        >
          <option value="" disabled>
            {field.placeholder ?? "Choose…"}
          </option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </NativeSelect>
        {help}
        {error}
      </div>
    );
  }

  if (field.type === "password") {
    const keySaved = capability.config.api_key_set && !form.draft.clearKey;
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={id}>{field.label}</Label>
        <Input
          id={id}
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => form.setValue(field.name, e.target.value)}
          placeholder={
            keySaved
              ? `Saved key${capability.config.api_key_hint ? ` ${capability.config.api_key_hint}` : ""}. Leave empty to keep it.`
              : (field.placeholder ?? "Paste your API key")
          }
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          disabled={disabled || form.draft.clearKey}
        />
        {capability.config.api_key_set && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {form.draft.clearKey ? (
              <>
                <span>The saved key will be removed when you save.</span>
                <Button
                  variant="link"
                  size="xs"
                  className="h-auto px-0"
                  onClick={() => form.setClearKey(false)}
                >
                  Keep it
                </Button>
              </>
            ) : (
              <Button
                variant="link"
                size="xs"
                className="h-auto px-0 text-muted-foreground"
                onClick={() => form.setClearKey(true)}
                disabled={disabled}
              >
                Remove saved key
              </Button>
            )}
          </div>
        )}
        {help}
        {error}
      </div>
    );
  }

  // text / url
  const listId = models && models.models.length > 0 ? `${id}-suggestions` : undefined;
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{field.label}</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          type={field.type === "url" ? "url" : "text"}
          inputMode={field.type === "url" ? "url" : undefined}
          spellCheck={false}
          autoComplete="off"
          value={typeof value === "string" ? value : ""}
          onChange={(e) => form.setValue(field.name, e.target.value)}
          placeholder={field.placeholder}
          list={listId}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          disabled={disabled}
          className={field.type === "url" || field.name === "model" ? "font-mono text-[13px]" : ""}
        />
        {models && (
          <Button
            variant="outline"
            onClick={models.onLoad}
            disabled={disabled || models.loading}
            className="shrink-0"
          >
            {models.loading ? <Spinner /> : <IconListSearch aria-hidden />}
            Load models
          </Button>
        )}
      </div>
      {listId && models && (
        <datalist id={listId}>
          {models.models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      )}
      {models?.message && (
        <p
          className={cn("text-xs", models.error ? "text-destructive" : "text-muted-foreground")}
          role={models.error ? "alert" : undefined}
        >
          {models.message}
        </p>
      )}
      {help}
      {error}
    </div>
  );
}
