/**
 * Minimal Server-Sent Events parser (WHATWG "event stream" format) for streamed fetch() responses.
 * EventSource can't be used because the chat endpoint is a POST.
 *
 * Handles LF, CRLF and CR line endings (also when a CRLF is split across chunks), events split
 * across chunks, several events in one chunk, multi-line `data:`, comments and a leading BOM.
 */

export interface SseEvent {
  /** "message" when the event had no `event:` field. */
  event: string;
  /** All `data:` lines joined with "\n". */
  data: string;
  id?: string;
}

export class SseParser {
  private buffer = "";
  private started = false;
  private eventName = "";
  private dataLines: string[] = [];
  private lastId: string | undefined;

  /** Feed decoded text; returns the events completed by it. */
  feed(chunk: string): SseEvent[] {
    if (!this.started && chunk.length > 0) {
      this.started = true;
      if (chunk.charCodeAt(0) === 0xfeff) chunk = chunk.slice(1);
    }
    this.buffer += chunk;
    const out: SseEvent[] = [];
    const buf = this.buffer;
    let start = 0;
    for (let i = 0; i < buf.length; i++) {
      const c = buf[i];
      if (c !== "\n" && c !== "\r") continue;
      // A trailing CR may be the first half of a CRLF: wait for the next chunk to decide.
      if (c === "\r" && i + 1 === buf.length) break;
      this.processLine(buf.slice(start, i), out);
      if (c === "\r" && buf[i + 1] === "\n") i++;
      start = i + 1;
    }
    this.buffer = buf.slice(start);
    return out;
  }

  /** Call when the stream ends: completes a last event that lacked its final blank line. */
  end(): SseEvent[] {
    const out: SseEvent[] = [];
    let rest = this.buffer;
    this.buffer = "";
    if (rest.endsWith("\r")) rest = rest.slice(0, -1);
    if (rest.length > 0) this.processLine(rest, out);
    this.dispatch(out);
    return out;
  }

  private processLine(line: string, out: SseEvent[]): void {
    if (line === "") {
      this.dispatch(out);
      return;
    }
    if (line.startsWith(":")) return; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.dataLines.push(value);
    else if (field === "event") this.eventName = value;
    else if (field === "id" && !value.includes("\0")) this.lastId = value;
    // "retry" and unknown fields are ignored.
  }

  private dispatch(out: SseEvent[]): void {
    if (this.dataLines.length > 0) {
      const ev: SseEvent = { event: this.eventName || "message", data: this.dataLines.join("\n") };
      if (this.lastId !== undefined) ev.id = this.lastId;
      out.push(ev);
    }
    this.eventName = "";
    this.dataLines = [];
  }
}

/** Reads a byte stream as SSE events. Decodes UTF-8 safely across chunk boundaries. */
export async function* readSse(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SseParser();
  const onAbort = () => void reader.cancel().catch(() => {});
  signal?.addEventListener("abort", onAbort, { once: true });
  let finished = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        finished = true;
        break;
      }
      yield* parser.feed(decoder.decode(value, { stream: true }));
    }
    const tail = decoder.decode();
    if (tail) yield* parser.feed(tail);
    yield* parser.end();
  } finally {
    signal?.removeEventListener("abort", onAbort);
    // The consumer stopped early: close the connection instead of leaving it open.
    if (!finished) await reader.cancel().catch(() => {});
  }
}
