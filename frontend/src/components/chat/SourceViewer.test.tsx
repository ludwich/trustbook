import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChunkContext, CodeContext, Message } from "@/api/types";
import {
  assistantMessage,
  chunk,
  citation,
  conversation,
  source,
  userMessage,
} from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { spaceBackend } from "@/test/spaceBackend";
import { codeScrollTop } from "@/lib/scroll";
import { CodeView, TextView } from "./SourceViewer";

const SPACE = "/spaces/sp_1";
const VIEWPORT = 400;

const CODE_CITATION = citation({
  n: 1,
  chunk_id: "ch_code",
  source_id: "src_repo",
  source_title: "requests",
  locator: { file: "src/requests/sessions.py", start_line: 250, end_line: 253, symbol: null },
});

/** Lines 201–450 of a file (a ±window as the backend sends for long files). */
function codeContext(over: Partial<CodeContext> = {}): CodeContext {
  return {
    file: "src/requests/sessions.py",
    commit: "0123456789abcdef",
    language: "python",
    first_line: 201,
    lines: Array.from({ length: 250 }, (_, i) =>
      i === 49 ? "    def request(self, method, url):" : `    x_${201 + i} = ${201 + i}`,
    ),
    highlight: [250, 253],
    ...over,
  };
}

function codeChunkContext(code = codeContext()): ChunkContext {
  return {
    chunk: chunk({
      id: "ch_code",
      source_id: "src_repo",
      source_title: "requests",
      text: "def request(self, method, url):",
      locator: CODE_CITATION.locator,
    }),
    source_type: "folder",
    view: "code",
    code,
  };
}

function answerCitingCode(): Message {
  return assistantMessage({
    content: {
      sentences: [{ text: "Session.request builds the call.", citations: [CODE_CITATION] }],
    },
    sources_used: [{ source_id: "src_repo", title: "requests" }],
  });
}

function repoSource() {
  return source({
    id: "src_repo",
    type: "folder",
    group: "code",
    title: "requests",
    commit_hash: "fedcba9876543210",
  });
}

afterEach(() => vi.restoreAllMocks());

function mockViewport() {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(VIEWPORT);
}

describe("source viewer: code (G6)", () => {
  it("shows monospace code with line numbers, the exact cited lines, file path and commit", async () => {
    mockViewport();
    spaceBackend({
      sources: [repoSource()],
      conversations: [conversation()],
      messages: { cv_1: [userMessage("How are calls made?"), answerCitingCode()] },
      contexts: { ch_code: codeChunkContext() },
    });
    const user = userEvent.setup();
    renderApp(SPACE);

    await user.click(
      await screen.findByRole("button", { name: /^Citation 1: requests, src\/requests/ }),
    );
    const viewer = await screen.findByRole("dialog", { name: "requests" });
    const code = await within(viewer).findByTestId("code-view");

    // Header: file path (monospace), cited lines and the short commit of the context.
    const file = within(viewer).getByTestId("viewer-file");
    expect(file).toHaveTextContent("src/requests/sessions.py, lines 250–253");
    expect(file).toHaveClass("font-mono");
    expect(within(viewer).getByTestId("viewer-commit")).toHaveTextContent("0123456");

    // Monospace, numbered from first_line.
    expect(code).toHaveClass("font-mono");
    const rows = code.querySelectorAll("[data-line]");
    expect(rows).toHaveLength(250);
    expect(rows[0]).toHaveAttribute("data-line", "201");
    expect(rows[0]).toHaveTextContent("201");
    expect(rows[249]).toHaveAttribute("data-line", "450");

    // Exactly lines 250–253 are highlighted, whole lines (no character offsets).
    const hit = [...code.querySelectorAll("[data-highlighted]")].map((r) =>
      r.getAttribute("data-line"),
    );
    expect(hit).toEqual(["250", "251", "252", "253"]);
    expect(code.querySelector('[data-line="250"] code')).toHaveTextContent(
      "def request(self, method, url):",
    );

    // Scrolled so the highlight is in view.
    expect(code.scrollTop).toBe(codeScrollTop(201, 250, [250, 253], VIEWPORT));
    expect(code.scrollTop).toBeGreaterThan(0);
  });

  it("is positioned before paint: scrollTop is set before any passive effect runs", () => {
    mockViewport();
    const seen: number[] = [];
    // Renders before CodeView, so with useEffect positioning its effect would run first and see 0.
    function PaintProbe() {
      useEffect(() => {
        const el = document.querySelector<HTMLElement>("[data-testid='code-view']");
        seen.push(el?.scrollTop ?? -1);
      });
      return null;
    }
    const code = codeContext();
    render(
      <>
        <PaintProbe />
        <CodeView code={code} />
      </>,
    );
    const expected = codeScrollTop(code.first_line, code.lines.length, code.highlight, VIEWPORT);
    expect(expected).toBeGreaterThan(0);
    expect(seen).toEqual([expected]);
  });

  it("never renders the file before the context has loaded", async () => {
    mockViewport();
    spaceBackend({
      sources: [repoSource()],
      conversations: [conversation()],
      messages: { cv_1: [userMessage("How are calls made?"), answerCitingCode()] },
    });
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.get("*/api/spaces/:sid/chunks/:id/context", async () => {
        await gate;
        await delay(1);
        return HttpResponse.json(codeChunkContext());
      }),
    );
    const user = userEvent.setup();
    renderApp(SPACE);
    await user.click(await screen.findByRole("button", { name: /^Citation 1: requests/ }));
    const viewer = await screen.findByRole("dialog", { name: "requests" });

    // Loading: a skeleton, no code (so the top of the file can't flash).
    expect(within(viewer).getByText("Loading the passage…")).toBeInTheDocument();
    expect(within(viewer).queryByTestId("code-view")).not.toBeInTheDocument();
    // Meanwhile the header uses the citation's locator and the source's commit.
    expect(within(viewer).getByTestId("viewer-label")).toHaveTextContent(
      "src/requests/sessions.py, lines 250–253",
    );

    release();
    const code = await within(viewer).findByTestId("code-view");
    expect(code.scrollTop).toBe(codeScrollTop(201, 250, [250, 253], VIEWPORT));
  });

  it("handles a short file whose highlight is at the top", () => {
    mockViewport();
    render(
      <CodeView
        code={codeContext({ first_line: 1, lines: ["a = 1", "", "b = 2"], highlight: [3, 3] })}
      />,
    );
    const code = screen.getByTestId("code-view");
    expect(code.scrollTop).toBe(0);
    expect(code.querySelectorAll("[data-line]")).toHaveLength(3);
    expect(code.querySelector("[data-highlighted]")).toHaveAttribute("data-line", "3");
    expect(screen.getByRole("region")).toHaveAccessibleName(
      "src/requests/sessions.py, line 3 highlighted",
    );
  });
});

describe("source viewer: text and states", () => {
  it("shows label, before, highlighted passage and after from the context", () => {
    render(
      <TextView
        text={{
          label: "Section: Install",
          before: "Intro text. ",
          highlight: "Run pip install requests.",
          after: " Then import it.",
        }}
      />,
    );
    expect(screen.getByTestId("cited-passage")).toHaveTextContent("Run pip install requests.");
    expect(screen.getByTestId("snapshot-text")).toHaveTextContent(
      "… Intro text. Run pip install requests. Then import it. …",
    );
  });

  it("shows the label in the header and a loading error with retry", async () => {
    let calls = 0;
    spaceBackend({
      sources: [source({ id: "src_report", title: "Annual report.pdf" })],
      conversations: [conversation()],
      messages: {
        cv_1: [
          userMessage("Q?"),
          assistantMessage({
            content: { sentences: [{ text: "Revenue grew.", citations: [citation({ n: 1 })] }] },
          }),
        ],
      },
    });
    server.use(
      http.get("*/api/spaces/:sid/chunks/:id/context", () => {
        calls++;
        if (calls === 1) return HttpResponse.json({ detail: "Index is busy." }, { status: 503 });
        return HttpResponse.json({
          chunk: chunk({ id: "ch_1", text: "Revenue grew 12%." }),
          source_type: "upload",
          view: "text",
          text: { label: "Page 3", before: "", highlight: "Revenue grew 12%.", after: "" },
        } satisfies ChunkContext);
      }),
    );
    const user = userEvent.setup();
    renderApp(SPACE);
    await user.click(await screen.findByRole("button", { name: /^Citation 1:/ }));
    const viewer = await screen.findByRole("dialog", { name: "Annual report.pdf" });
    expect(await within(viewer).findByText("Index is busy.")).toBeInTheDocument();
    await user.click(within(viewer).getByRole("button", { name: "Try again" }));
    expect(await within(viewer).findByTestId("cited-passage")).toHaveTextContent(
      "Revenue grew 12%.",
    );
    expect(within(viewer).getByTestId("viewer-label")).toHaveTextContent("· Page 3");
    await waitFor(() => expect(calls).toBe(2));
  });

  it("falls back to the passage alone when the context has no view data", async () => {
    spaceBackend({
      sources: [repoSource()],
      conversations: [conversation()],
      messages: { cv_1: [userMessage("How are calls made?"), answerCitingCode()] },
      contexts: { ch_code: { ...codeChunkContext(), code: codeContext({ lines: [] }) } },
    });
    const user = userEvent.setup();
    renderApp(SPACE);
    await user.click(await screen.findByRole("button", { name: /^Citation 1: requests/ }));
    const viewer = await screen.findByRole("dialog", { name: "requests" });
    expect(await within(viewer).findByTestId("cited-passage")).toHaveTextContent(
      "def request(self, method, url):",
    );
    expect(within(viewer).queryByTestId("code-view")).not.toBeInTheDocument();
  });
});
