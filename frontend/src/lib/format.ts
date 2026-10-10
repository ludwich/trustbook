/** "about 5 min left" style text. Returns null when there is no estimate. */
export function formatEta(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  if (seconds < 60) return "less than a minute left";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 90) return `about ${minutes} min left`;
  const h = Math.floor(minutes / 60);
  const m = Math.round((minutes % 60) / 5) * 5;
  return m ? `about ${h} h ${m} min left` : `about ${h} h left`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  const mb = kb / 1024;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { year: "numeric", month: "short", day: "numeric" });
}

/** "2 min ago" style relative time. */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const diff = Math.round((now.getTime() - d.getTime()) / 1000);
  if (Number.isNaN(diff)) return iso;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return formatDate(iso);
}

export function formatNumber(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return "Not measured";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function roundSpeed(n: number): string {
  return n >= 10
    ? Math.round(n).toLocaleString("en-US")
    : n.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

/**
 * Speech-to-text speed from a real-time factor (seconds of processing per second of audio).
 * 0.04 → "25× faster than real time", 2.5 → "2.5× slower than real time".
 */
export function formatRealtimeFactor(factor: number | null | undefined): string {
  if (factor === null || factor === undefined || !Number.isFinite(factor) || factor <= 0) {
    return "Not measured";
  }
  if (factor >= 0.95 && factor <= 1.05) return "about real time";
  if (factor < 1) return `${roundSpeed(1 / factor)}× faster than real time`;
  return `${roundSpeed(factor)}× slower than real time`;
}

interface ComputerCpu {
  model: string | null;
  logical: number;
  usable: number;
}

interface ComputerRuntime {
  torch_threads: number | null;
  embedding_dtype: string | null;
}

/**
 * Compact "This computer" summary, e.g.
 * "AMD Ryzen AI Max+ 395 · uses 32 of 32 CPUs · 16 model threads · BF16".
 * Returns null when neither part is known (results from before calibration v3).
 */
export function formatComputer(
  cpu: ComputerCpu | null | undefined,
  runtime: ComputerRuntime | null | undefined,
): string | null {
  if (!cpu && !runtime) return null;
  const parts: string[] = [];
  if (cpu) {
    const model = cpu.model?.trim();
    if (model) parts.push(model);
    parts.push(`uses ${cpu.usable} of ${plural(cpu.logical, "CPU")}`);
  }
  if (runtime) {
    if (runtime.torch_threads !== null) parts.push(plural(runtime.torch_threads, "model thread"));
    parts.push(runtime.embedding_dtype === "bfloat16" ? "BF16" : "FP32");
  }
  return parts.join(" · ");
}
