import { HttpResponse } from "msw";

/** One SSE event in wire format. */
export function sseEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A finished event stream, sent as the given chunks. */
export function sseResponse(chunks: string[]): HttpResponse<ReadableStream<Uint8Array>> {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
}

/** An event stream the test feeds step by step, to check what shows while it is open. */
export function controlledSse() {
  const enc = new TextEncoder();
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
  const queued: string[] = [];
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
      for (const q of queued) c.enqueue(enc.encode(q));
    },
  });
  return {
    response: () => new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } }),
    push(text: string) {
      if (controller) controller.enqueue(enc.encode(text));
      else queued.push(text);
    },
    close() {
      controller?.close();
    },
  };
}
