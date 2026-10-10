import {
  IconArrowLeft,
  IconArrowRight,
  IconCheck,
  IconDeviceLaptop,
  IconPlugConnected,
  IconWorldUpload,
} from "@tabler/icons-react";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "@/api/client";
import {
  useCapabilities,
  useCompleteSetup,
  useTestCapability,
  useUpdateCapability,
} from "@/api/queries";
import type { Capability, TestResult } from "@/api/types";
import { CapabilityFields } from "@/components/capabilities/CapabilityFields";
import { useCapabilityDraft, type CapabilityDraftApi } from "@/components/capabilities/draft";
import { LocalityBadge } from "@/components/capabilities/LocalityBadge";
import { TestResultView } from "@/components/capabilities/TestResultView";
import { useModelSuggestions } from "@/components/capabilities/useModelSuggestions";
import { QueryError } from "@/components/common/QueryError";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { localityOfUrl } from "@/lib/locality";
import { cn, errorMessage } from "@/lib/utils";

type Step = 1 | 2 | 3;

const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: "Chat model" },
  { n: 2, label: "Optional extras" },
  { n: 3, label: "Done" },
];

export function SetupPage() {
  const caps = useCapabilities();
  const [step, setStep] = useState<Step>(1);

  const find = (id: Capability["id"]) => caps.data?.find((c) => c.id === id);
  const chat = find("chat");
  const stt = find("stt");
  const search = find("web_search");

  return (
    <div className="mx-auto w-full max-w-2xl px-5 py-8">
      <h1 className="text-xl font-semibold">Set up Trustbook</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Trustbook answers only from sources you add. It needs a chat model to write those answers.
      </p>

      <Stepper current={step} />

      {caps.isPending && <Skeleton className="h-80" />}
      {caps.isError && (
        <QueryError
          title="Could not load settings"
          error={caps.error}
          onRetry={() => void caps.refetch()}
        />
      )}
      {caps.isSuccess && !chat && (
        <QueryError error={new Error("The chat model settings are missing.")} />
      )}

      {chat && step === 1 && <ChatStep capability={chat} onDone={() => setStep(2)} />}
      {chat && step === 2 && (
        <ExtrasStep stt={stt} search={search} onBack={() => setStep(1)} onDone={() => setStep(3)} />
      )}
      {chat && step === 3 && (
        <DoneStep chat={chat} stt={stt} search={search} onBack={() => setStep(2)} />
      )}
    </div>
  );
}

function Stepper({ current }: { current: Step }) {
  return (
    <ol className="my-6 flex items-center gap-2 text-sm" aria-label="Setup steps">
      {STEPS.map((s, i) => {
        const done = s.n < current;
        const active = s.n === current;
        return (
          <li
            key={s.n}
            className="flex items-center gap-2"
            aria-current={active ? "step" : undefined}
          >
            {i > 0 && <span aria-hidden className="h-px w-8 bg-border" />}
            <span
              className={cn(
                "flex size-6 items-center justify-center rounded-full border text-xs font-semibold",
                active && "border-primary bg-primary text-primary-foreground",
                done && "border-primary text-primary",
                !active && !done && "text-muted-foreground",
              )}
            >
              {done ? <IconCheck className="size-3.5" aria-label="Done" /> : s.n}
            </span>
            <span className={cn(active ? "font-medium" : "text-muted-foreground")}>{s.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

function StepCard({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description: ReactNode;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <section className="rounded-lg border bg-card shadow-xs" aria-labelledby="step-title">
      <div className="border-b px-5 py-4">
        <h2 id="step-title" className="text-base font-semibold">
          {title}
        </h2>
        <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="grid gap-5 px-5 py-5">{children}</div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t px-5 py-3">
        {footer}
      </div>
    </section>
  );
}

/** Where the data goes for the URL in the draft. */
function DraftLocality({ url }: { url: string }) {
  const loc = url ? localityOfUrl(url) : null;
  if (!loc) return null;
  return loc.locality === "local" ? (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <IconDeviceLaptop className="size-3.5" aria-hidden />
      Local: runs on this computer.
    </p>
  ) : (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <IconWorldUpload className="size-3.5 text-warning" aria-hidden />
      Remote: your questions and the relevant passages from your sources are sent to{" "}
      <span className="font-mono">{loc.host}</span>.
    </p>
  );
}

// ---- Step 1 -----------------------------------------------------------------

function ChatStep({ capability, onDone }: { capability: Capability; onDone: () => void }) {
  const form = useCapabilityDraft(capability);
  const models = useModelSuggestions(capability, form);
  const test = useTestCapability(capability.id);
  const save = useUpdateCapability(capability.id);
  const [result, setResult] = useState<{ sig: string; result: TestResult } | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  const sig = JSON.stringify(form.update);
  const current = result && result.sig === sig ? result.result : null;
  const stale = result !== null && current === null;
  const testedOk = current?.ok === true;
  const testFailed = current?.ok === false;
  // Re-running the wizard with a working, unchanged config: no need to test again.
  const alreadyWorking = capability.status === "ready" && !form.dirty;

  const runTest = () => {
    if (form.missing.length) {
      setShowErrors(true);
      return;
    }
    const draftSig = sig;
    test.mutate(form.update, { onSuccess: (r) => setResult({ sig: draftSig, result: r }) });
  };

  const doSave = () => {
    if (form.missing.length) {
      setShowErrors(true);
      return;
    }
    save.mutate(form.update, { onSuccess: onDone });
  };

  return (
    <StepCard
      title="Connect a chat model"
      description="Pick where your model runs. Test the connection before you save."
      footer={
        <>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={runTest} disabled={test.isPending || save.isPending}>
              {test.isPending ? <Spinner label="Testing" /> : <IconPlugConnected aria-hidden />}
              Test
            </Button>
          </div>
          <div className="flex items-center gap-2">
            {testFailed && (
              <Button variant="ghost" onClick={doSave} disabled={save.isPending}>
                Save anyway
              </Button>
            )}
            {alreadyWorking && !testedOk ? (
              <Button onClick={onDone}>
                Continue
                <IconArrowRight aria-hidden />
              </Button>
            ) : (
              <Button
                onClick={doSave}
                disabled={!testedOk || save.isPending}
                title={testedOk ? undefined : "Run a successful test first"}
              >
                {save.isPending && <Spinner label="Saving" />}
                Save and continue
                <IconArrowRight aria-hidden />
              </Button>
            )}
          </div>
        </>
      }
    >
      <CapabilityFields
        capability={capability}
        form={form}
        idPrefix="setup-chat"
        showErrors={showErrors}
        models={models}
      />
      <DraftLocality
        url={typeof form.draft.values.base_url === "string" ? form.draft.values.base_url : ""}
      />

      <div aria-live="polite" className="grid gap-1.5">
        {current && <TestResultView result={current} />}
        {stale && (
          <p className="text-sm text-muted-foreground">
            You changed the settings after the last test. Test again before saving.
          </p>
        )}
        {!result && !alreadyWorking && (
          <p className="text-sm text-muted-foreground">
            Test sends a short message ("Reply with OK") to check the model answers.
          </p>
        )}
        {testFailed && (
          <p className="text-sm text-muted-foreground">
            Fix the settings and test again, or save anyway and fix it later in Settings.
          </p>
        )}
        {test.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not run the test: {errorMessage(test.error)}
          </p>
        )}
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not save: {errorMessage(save.error)}
          </p>
        )}
      </div>
    </StepCard>
  );
}

// ---- Step 2 -----------------------------------------------------------------

const STT_LABELS = {
  mode: { bundled: "Use built-in speech-to-text", endpoint: "Use my own endpoint" },
};
const SEARCH_LABELS = {
  provider: { searxng: "Built-in search (SearXNG)", brave: "Brave Search", tavily: "Tavily" },
};

/** Stand-in for a capability the backend did not return: no fields, so never dirty. */
function placeholder(id: Capability["id"]): Capability {
  return {
    id,
    label: id,
    description: "",
    kind: "bundled",
    required: false,
    hidden: true,
    contract: "",
    fields: [],
    presets: [],
    providers: [],
    config: {
      enabled: true,
      preset: null,
      base_url: null,
      model: null,
      api_version: null,
      api_key_set: false,
      api_key_hint: null,
      use_chat: null,
      provider: null,
      mode: null,
    },
    locality: null,
    status: "ready",
    status_detail: "",
    last_test: null,
    bundled: null,
  };
}

function ExtrasStep({
  stt,
  search,
  onBack,
  onDone,
}: {
  stt: Capability | undefined;
  search: Capability | undefined;
  onBack: () => void;
  onDone: () => void;
}) {
  const sttCap = stt ?? placeholder("stt");
  const searchCap = search ?? placeholder("web_search");
  const sttForm = useCapabilityDraft(sttCap);
  const searchForm = useCapabilityDraft(searchCap);
  const saveStt = useUpdateCapability("stt");
  const saveSearch = useUpdateCapability("web_search");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  const onContinue = async () => {
    const pending = [
      { form: sttForm, save: saveStt },
      { form: searchForm, save: saveSearch },
    ].filter((p) => p.form.dirty);
    if (pending.some((p) => p.form.missing.length > 0)) {
      setShowErrors(true);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      for (const p of pending) await p.save.mutateAsync(p.form.update);
      onDone();
    } catch (e) {
      setError(`Could not save: ${errorMessage(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <StepCard
      title="Optional extras"
      description="The defaults work without any accounts. You can change these later in Settings."
      footer={
        <>
          <Button variant="ghost" onClick={onBack} disabled={saving}>
            <IconArrowLeft aria-hidden />
            Back
          </Button>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={onDone} disabled={saving}>
              Skip for now
            </Button>
            <Button onClick={() => void onContinue()} disabled={saving}>
              {saving && <Spinner label="Saving" />}
              Continue
              <IconArrowRight aria-hidden />
            </Button>
          </div>
        </>
      }
    >
      {stt && (
        <ExtraSection
          capability={stt}
          form={sttForm}
          title="Speech to text"
          description="Turns audio and videos without captions into text."
          optionLabels={STT_LABELS}
          showErrors={showErrors}
        />
      )}
      {search && (
        <ExtraSection
          capability={search}
          form={searchForm}
          title="Web search"
          description="Helps you find web pages to add as sources. Chat answers never search the web."
          optionLabels={SEARCH_LABELS}
          showErrors={showErrors}
        />
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </StepCard>
  );
}

function ExtraSection({
  capability,
  form,
  title,
  description,
  optionLabels,
  showErrors,
}: {
  capability: Capability;
  form: CapabilityDraftApi;
  title: string;
  description: string;
  optionLabels: Record<string, Record<string, string>>;
  showErrors: boolean;
}) {
  const test = useTestCapability(capability.id);
  const [result, setResult] = useState<TestResult | null>(null);

  return (
    <fieldset className="grid gap-3 rounded-md border px-4 pt-1 pb-3">
      <legend className="px-1 text-sm font-semibold">{title}</legend>
      <p className="text-sm text-muted-foreground">{description}</p>
      <CapabilityFields
        capability={capability}
        form={form}
        idPrefix={`setup-${capability.id}`}
        optionLabels={optionLabels}
        showErrors={showErrors}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            setResult(null);
            test.mutate(form.update, { onSuccess: setResult });
          }}
          disabled={test.isPending || form.missing.length > 0}
        >
          {test.isPending ? <Spinner label="Testing" /> : <IconPlugConnected aria-hidden />}
          Test
        </Button>
        <div aria-live="polite" className="min-w-0 flex-1">
          {result && <TestResultView result={result} />}
          {test.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(test.error)}
            </p>
          )}
        </div>
      </div>
    </fieldset>
  );
}

// ---- Step 3 -----------------------------------------------------------------

function DoneStep({
  chat,
  stt,
  search,
  onBack,
}: {
  chat: Capability;
  stt: Capability | undefined;
  search: Capability | undefined;
  onBack: () => void;
}) {
  const complete = useCompleteSetup();
  const navigate = useNavigate();

  const finish = () =>
    complete.mutate(undefined, { onSuccess: () => void navigate("/", { replace: true }) });

  const chatSummary = [
    chat.config.model,
    chat.presets.find((p) => p.id === chat.config.preset)?.label,
  ]
    .filter(Boolean)
    .join(" · ");
  const sttSummary =
    stt?.config.mode === "endpoint"
      ? `Your endpoint${stt.config.model ? ` · ${stt.config.model}` : ""}`
      : "Built-in";
  const searchSummary =
    search?.providers.find((p) => p.value === search.config.provider)?.label ??
    (search?.config.provider && search.config.provider !== "searxng"
      ? search.config.provider
      : "Built-in (SearXNG)");

  const notConfigured = complete.error instanceof ApiError && complete.error.status === 409;

  return (
    <StepCard
      title="You're ready"
      description="Next, create a space and add your first sources."
      footer={
        <>
          <Button variant="ghost" onClick={onBack} disabled={complete.isPending}>
            <IconArrowLeft aria-hidden />
            Back
          </Button>
          <Button onClick={finish} disabled={complete.isPending}>
            {complete.isPending && <Spinner label="Finishing" />}
            Go to spaces
            <IconArrowRight aria-hidden />
          </Button>
        </>
      }
    >
      <dl className="grid grid-cols-[max-content_1fr] items-center gap-x-6 gap-y-2.5 text-sm">
        <dt className="text-muted-foreground">Chat model</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <span>{chatSummary || "Not set up"}</span>
          <LocalityBadge locality={chat.locality} />
        </dd>
        {stt && (
          <>
            <dt className="text-muted-foreground">Speech to text</dt>
            <dd className="flex items-center gap-2">
              <span>{sttSummary}</span>
              <LocalityBadge locality={stt.locality} />
            </dd>
          </>
        )}
        {search && (
          <>
            <dt className="text-muted-foreground">Web search</dt>
            <dd className="flex items-center gap-2">
              <span>{searchSummary}</span>
              <LocalityBadge locality={search.locality} />
            </dd>
          </>
        )}
      </dl>
      {complete.isError && (
        <p role="alert" className="text-sm text-destructive">
          {notConfigured
            ? "The chat model is not set up yet. Go back to step 1 and save it."
            : `Could not finish setup: ${errorMessage(complete.error)}`}
        </p>
      )}
    </StepCard>
  );
}
