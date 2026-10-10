import { describe, expect, it } from "vitest";
import { readSse, SseParser, type SseEvent } from "./sse";

function feedAll(chunks: string[]): SseEvent[] {
  const p = new SseParser();
  const out: SseEvent[] = [];
  for (const c of chunks) out.push(...p.feed(c));
  out.push(...p.end());
  return out;
}

function byteStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(c);
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SseEvent[]> {
  const out: SseEvent[] = [];
  for await (const ev of readSse(stream)) out.push(ev);
  return out;
}

const BOM = String.fromCharCode(0xfeff);

const STREAM =
  'event: status\ndata: {"stage":"retrieving"}\n\n' +
  'event: sentence\ndata: {"index":0,"text":"Hello."}\n\n' +
  'event: done\ndata: {"message":{"id":"m1"}}\n\n';

const EXPECTED: SseEvent[] = [
  { event: "status", data: '{"stage":"retrieving"}' },
  { event: "sentence", data: '{"index":0,"text":"Hello."}' },
  { event: "done", data: '{"message":{"id":"m1"}}' },
];

describe("SseParser", () => {
  it("parses several events in one chunk", () => {
    expect(feedAll([STREAM])).toEqual(EXPECTED);
  });

  it("parses events split mid-event, mid-field and mid-line at every position", () => {
    for (let i = 1; i < STREAM.length; i++) {
      expect(feedAll([STREAM.slice(0, i), STREAM.slice(i)])).toEqual(EXPECTED);
    }
  });

  it("parses a stream fed one character at a time", () => {
    expect(feedAll(STREAM.split(""))).toEqual(EXPECTED);
  });

  it("handles CRLF line endings, including a CRLF split across chunks", () => {
    const crlf = STREAM.replace(/\n/g, "\r\n");
    expect(feedAll([crlf])).toEqual(EXPECTED);
    for (let i = 1; i < crlf.length; i++) {
      expect(feedAll([crlf.slice(0, i), crlf.slice(i)])).toEqual(EXPECTED);
    }
  });

  it("handles bare CR line endings", () => {
    expect(feedAll([STREAM.replace(/\n/g, "\r")])).toEqual(EXPECTED);
  });

  it("does not emit an event until its blank line arrives", () => {
    const p = new SseParser();
    expect(p.feed('event: status\ndata: {"stage":"generating"}\n')).toEqual([]);
    expect(p.feed("\n")).toEqual([{ event: "status", data: '{"stage":"generating"}' }]);
  });

  it("joins multi-line data, ignores comments and defaults the event name", () => {
    const events = feedAll([": keep-alive\n\ndata: line one\ndata:line two\nid: 7\n\n"]);
    expect(events).toEqual([{ event: "message", data: "line one\nline two", id: "7" }]);
  });

  it("ignores events without data and strips a leading BOM", () => {
    expect(feedAll([`${BOM}event: ping\n\nevent: x\ndata: 1\n\n`])).toEqual([
      { event: "x", data: "1" },
    ]);
  });

  it("completes a last event that lacks its final blank line", () => {
    expect(feedAll(['event: error\ndata: {"message":"boom"}'])).toEqual([
      { event: "error", data: '{"message":"boom"}' },
    ]);
  });
});

describe("readSse", () => {
  it("decodes UTF-8 split across byte chunks", async () => {
    const bytes = new TextEncoder().encode('event: sentence\ndata: {"text":"Malmö – Åre ✓"}\n\n');
    // Split inside the multi-byte "ö".
    const cut = bytes.indexOf(0xc3) + 1;
    const events = await collect(byteStream([bytes.slice(0, cut), bytes.slice(cut)]));
    expect(events).toEqual([{ event: "sentence", data: '{"text":"Malmö – Åre ✓"}' }]);
  });

  it("reads a chunked byte stream with several events per chunk", async () => {
    const bytes = new TextEncoder().encode(STREAM.replace(/\n/g, "\r\n"));
    const chunks = [bytes.slice(0, 7), bytes.slice(7, 60), bytes.slice(60)];
    expect(await collect(byteStream(chunks))).toEqual(EXPECTED);
  });
});
