import type {
  Capability,
  CapabilityConfig,
  Chunk,
  Citation,
  Conversation,
  Field,
  IdeationNode,
  IdeationSession,
  Job,
  JobList,
  Message,
  Overview,
  Preset,
  Settings,
  Source,
  Space,
} from "@/api/types";

export const CHAT_PRESETS: Preset[] = [
  {
    id: "openai",
    label: "OpenAI",
    adapter: "openai_compatible",
    base_url: "https://api.openai.com/v1",
    needs_api_key: true,
  },
  {
    id: "azure_openai",
    label: "Azure OpenAI",
    adapter: "azure_openai",
    base_url: null,
    needs_api_key: true,
  },
  {
    id: "anthropic",
    label: "Anthropic",
    adapter: "anthropic",
    base_url: "https://api.anthropic.com",
    needs_api_key: true,
  },
  {
    id: "ollama",
    label: "Ollama",
    adapter: "openai_compatible",
    base_url: "http://host.docker.internal:11434/v1",
    needs_api_key: false,
  },
  {
    id: "llama_server",
    label: "llama-server",
    adapter: "openai_compatible",
    base_url: "http://host.docker.internal:8080/v1",
    needs_api_key: false,
  },
  {
    id: "custom",
    label: "Other (OpenAI-compatible)",
    adapter: "openai_compatible",
    base_url: null,
    needs_api_key: false,
  },
];

export const ENDPOINT_FIELDS: Field[] = [
  { name: "preset", label: "Provider", type: "select", required: true },
  { name: "base_url", label: "Base URL", type: "url", required: true, placeholder: "https://…/v1" },
  { name: "api_key", label: "API key", type: "password", required: false },
  { name: "model", label: "Model", type: "text", required: true },
  {
    name: "api_version",
    label: "API version",
    type: "text",
    required: false,
    placeholder: "2024-10-21",
  },
];

export function config(over: Partial<CapabilityConfig> = {}): CapabilityConfig {
  return {
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
    ...over,
  };
}

export function capability(over: Partial<Capability> & Pick<Capability, "id">): Capability {
  return {
    label: over.id,
    description: "",
    kind: "endpoint",
    required: false,
    hidden: false,
    contract: "",
    fields: [],
    presets: [],
    providers: [],
    config: config(),
    locality: null,
    status: "ready",
    status_detail: "",
    last_test: null,
    bundled: null,
    ...over,
  };
}

export function chatCapability(over: Partial<Capability> = {}): Capability {
  return capability({
    id: "chat",
    label: "Chat model",
    description: "Writes answers, overviews and ideation questions.",
    kind: "endpoint",
    required: true,
    contract: "openai_chat",
    fields: ENDPOINT_FIELDS,
    presets: CHAT_PRESETS,
    status: "not_configured",
    status_detail: "Choose a provider and model.",
    ...over,
  });
}

export function gateCapability(over: Partial<Capability> = {}): Capability {
  return capability({
    id: "gate",
    label: "Router",
    description: "Makes quick fixed-choice decisions.",
    fields: [
      { name: "use_chat", label: "Use the chat model", type: "toggle", required: false },
      ...ENDPOINT_FIELDS,
    ],
    presets: CHAT_PRESETS,
    config: config({ use_chat: true }),
    ...over,
  });
}

export function embeddingsCapability(over: Partial<Capability> = {}): Capability {
  return capability({
    id: "embeddings",
    label: "Embeddings",
    description: "Turns text into vectors for search.",
    kind: "bundled",
    locality: "local",
    status: "ready",
    status_detail: "Built-in model loaded.",
    bundled: {
      model_id: "google/embeddinggemma-2",
      revision: "914f7f89142e0000",
      license: "Apache-2.0",
      present: true,
      fallback: false,
    },
    ...over,
  });
}

export function groundingCapability(over: Partial<Capability> = {}): Capability {
  return capability({
    id: "grounding",
    label: "Answer checking",
    description: "Checks every sentence against your sources.",
    kind: "bundled",
    locality: "local",
    fields: [{ name: "enabled", label: "Check answers", type: "toggle", required: false }],
    bundled: {
      model_id: "vectara/hallucination_evaluation_model",
      revision: "de9a5e6ba9ba",
      license: "Apache-2.0",
      present: true,
      fallback: false,
    },
    ...over,
  });
}

export function sttCapability(over: Partial<Capability> = {}): Capability {
  return capability({
    id: "stt",
    label: "Speech to text",
    description: "Transcribes audio.",
    kind: "bundled_with_endpoint_override",
    locality: "local",
    fields: [
      {
        name: "mode",
        label: "Use",
        type: "select",
        required: true,
        options: [
          { value: "bundled", label: "Built-in" },
          { value: "endpoint", label: "Endpoint" },
        ],
      },
      { name: "preset", label: "Provider", type: "select", required: false },
      { name: "base_url", label: "Base URL", type: "url", required: false },
      { name: "api_key", label: "API key", type: "password", required: false },
      { name: "model", label: "Model", type: "text", required: false },
    ],
    presets: [
      {
        id: "openai",
        label: "OpenAI",
        adapter: "openai_compatible",
        base_url: "https://api.openai.com/v1",
        needs_api_key: true,
      },
      {
        id: "custom",
        label: "Other",
        adapter: "openai_compatible",
        base_url: null,
        needs_api_key: false,
      },
    ],
    config: config({ mode: "bundled" }),
    bundled: {
      model_id: "Cactus-Compute/whistle",
      revision: "ca5287601bef",
      license: "Apache-2.0",
      present: true,
      fallback: false,
    },
    ...over,
  });
}

export function searchCapability(over: Partial<Capability> = {}): Capability {
  return capability({
    id: "web_search",
    label: "Web search",
    description: "Finds web pages to add as sources.",
    kind: "bundled_with_endpoint_override",
    locality: "remote",
    fields: [
      { name: "provider", label: "Search with", type: "select", required: true },
      { name: "api_key", label: "API key", type: "password", required: false },
    ],
    providers: [
      { value: "searxng", label: "SearXNG (built-in)" },
      { value: "brave", label: "Brave" },
      { value: "tavily", label: "Tavily" },
    ],
    config: config({ provider: "searxng" }),
    ...over,
  });
}

export function ttsCapability(): Capability {
  return capability({ id: "tts", label: "Text to speech", hidden: true, status: "not_configured" });
}

export function allCapabilities(): Capability[] {
  return [
    chatCapability(),
    gateCapability(),
    embeddingsCapability(),
    groundingCapability(),
    sttCapability(),
    searchCapability(),
    ttsCapability(),
  ];
}

export function space(over: Partial<Space> = {}): Space {
  const slug = over.slug ?? "acme-audit";
  return {
    id: "sp_1",
    slug,
    name: "Acme audit",
    description: "Security review for Acme",
    created_at: "2026-10-01T09:00:00Z",
    schema_version: 1,
    embedding: { model: "google/embeddinggemma-2", revision: "914f7f89142e" },
    folder: `/data/spaces/${slug}`,
    source_count: 0,
    ...over,
  };
}

export function job(over: Partial<Job> = {}): Job {
  return {
    id: "job_1",
    kind: "ingest",
    title: "Reading report.pdf",
    space_id: "sp_1",
    space_name: "Acme audit",
    status: "queued",
    progress: null,
    position: 0,
    error: null,
    created_at: "2026-10-10T08:00:00Z",
    started_at: null,
    finished_at: null,
    ...over,
  };
}

export function jobList(jobs: Job[] = [], over: Partial<JobList["summary"]> = {}): JobList {
  return {
    jobs,
    summary: {
      queued: jobs.filter((j) => j.status === "queued").length,
      running: jobs.filter((j) => j.status === "running").length,
      paused: jobs.filter((j) => j.status === "paused").length,
      eta_seconds: null,
      workers: 2,
      ...over,
    },
  };
}

// ---- Phase 1 ----------------------------------------------------------------

export function settings(over: Partial<Settings> = {}): Settings {
  return {
    power: "normal",
    setup_completed: true,
    workers: 2,
    thresholds: { refuse: 0.15, verify: 0.5 },
    hints_dismissed: [],
    ...over,
  };
}

export function source(over: Partial<Source> & Pick<Source, "id">): Source {
  return {
    type: "upload",
    title: `${over.id}.pdf`,
    origin: null,
    group: "documents",
    status: "ready",
    progress: null,
    failure_reason: null,
    failure_next_step: null,
    quality_warning: null,
    enabled: true,
    snapshot_at: null,
    commit_hash: null,
    stats: {},
    created_at: "2026-10-10T08:00:00Z",
    job_id: null,
    ...over,
  };
}

export function citation(over: Partial<Citation> & Pick<Citation, "n">): Citation {
  return {
    chunk_id: `ch_${over.n}`,
    source_id: "src_report",
    source_title: "Annual report.pdf",
    locator: { pages: [3, 4] },
    ...over,
  };
}

export function conversation(over: Partial<Conversation> = {}): Conversation {
  return {
    id: "cv_1",
    title: "Revenue questions",
    created_at: "2026-10-10T09:00:00Z",
    updated_at: "2026-10-10T09:00:00Z",
    ...over,
  };
}

export function userMessage(text: string, id = "m_user"): Message {
  return {
    id,
    role: "user",
    created_at: "2026-10-10T09:00:00Z",
    content: { text },
    badge: null,
    removed_sentences: [],
    sources_used: [],
  };
}

export function assistantMessage(over: Partial<Message> = {}): Message {
  return {
    id: "m_answer",
    role: "assistant",
    created_at: "2026-10-10T09:00:05Z",
    content: { sentences: [] },
    badge: null,
    removed_sentences: [],
    sources_used: [],
    ...over,
  };
}

export function overview(over: Partial<Overview> = {}): Overview {
  return {
    enabled: true,
    status: "none",
    sentences: [],
    questions: [],
    generated_at: null,
    stale: false,
    estimate: null,
    ...over,
  };
}

export function chunk(over: Partial<Chunk> & Pick<Chunk, "id">): Chunk {
  return {
    source_id: "src_report",
    source_title: "Annual report.pdf",
    text: "",
    start_offset: 0,
    end_offset: 0,
    locator: {},
    ...over,
  };
}

// ---- Phase 4: ideation ----------------------------------------------------------

export function ideationNode(over: Partial<IdeationNode> & Pick<IdeationNode, "id">): IdeationNode {
  return {
    parent_id: null,
    ordinal: 0,
    depth: 0,
    title: `Branch ${over.id}`,
    status: "tentative",
    origin: "seed",
    question: null,
    recommendation: null,
    final_answer: null,
    answer_source: null,
    asked_at: null,
    answered_at: null,
    dismissible: over.status === undefined || over.status === "tentative",
    ...over,
  };
}

/** Progress and completion as the server computes them (tentative and open count as open). */
export function withProgress(session: IdeationSession): IdeationSession {
  const live = session.nodes.filter((n) => n.status !== "dismissed");
  const resolved = live.filter((n) => n.status === "resolved").length;
  const open = live.length - resolved;
  return { ...session, progress: { resolved, open }, complete: live.length > 0 && open === 0 };
}

export function ideationSession(over: Partial<IdeationSession> = {}): IdeationSession {
  return withProgress({
    id: "is_1",
    title: "Puffin walks",
    idea: "Guided puffin walks on Gull Island for birdwatchers",
    status: "active",
    progress: { resolved: 0, open: 0 },
    complete: false,
    created_at: "2026-10-10T09:00:00Z",
    updated_at: "2026-10-10T09:00:00Z",
    finished_at: null,
    brief_at: null,
    nodes: [],
    current_node_id: null,
    risks: [],
    ...over,
  });
}
