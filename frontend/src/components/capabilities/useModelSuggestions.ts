import { useState } from "react";
import { useListModels } from "@/api/queries";
import type { Capability } from "@/api/types";
import { errorMessage } from "@/lib/utils";
import type { ModelSuggestions } from "./CapabilityFields";
import type { CapabilityDraftApi } from "./draft";
import { hasField } from "./draft";

/** "Load models" support for capabilities that have a model field and presets. */
export function useModelSuggestions(
  capability: Capability,
  form: CapabilityDraftApi,
): ModelSuggestions | undefined {
  const list = useListModels(capability.id);
  const [models, setModels] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState(false);

  if (!hasField(capability, "model") || capability.presets.length === 0) return undefined;

  return {
    models,
    loading: list.isPending,
    message,
    error,
    onLoad: () => {
      setMessage(null);
      list.mutate(form.update, {
        onSuccess: (res) => {
          setModels(res.models);
          setError(false);
          setMessage(
            res.models.length === 0
              ? "This endpoint did not list any models. Type the model name."
              : `${res.models.length} ${res.models.length === 1 ? "model" : "models"} found. Start typing to pick one.`,
          );
        },
        onError: (e) => {
          setModels([]);
          setError(true);
          setMessage(errorMessage(e));
        },
      });
    },
  };
}
