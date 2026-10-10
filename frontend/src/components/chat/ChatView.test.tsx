import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Message } from "@/api/types";
import {
  assistantMessage,
  chunk,
  citation,
  conversation,
  overview,
  source,
  userMessage,
} from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { spaceBackend } from "@/test/spaceBackend";
import { controlledSse, sseEvent, sseResponse } from "@/test/sse";

const SPACE = "/spaces/sp_1";

const READY_SOURCES = [
  source({ id: "src_report", title: "Annual report.pdf" }),
  source({ id: "src_notes", type: "paste", title: "Board notes", enabled: false }),
];

const C1 = citation({ n: 1 });
const C2 = citation({
  n: 2,
  chunk_id: "ch_2",
  source_id: "src_notes",
  source_title: "Board notes",
  locator: { heading: "Budget" },
});

const SNAPSHOT_TEXT =
  "Introduction.\n\nRevenue grew 12% in 2025, driven by the Nordic region.\n\nOutlook follows.";
const PASSAGE = "Revenue grew 12% in 2025, driven by the Nordic region.";
const PASSAGE_START = SNAPSHOT_TEXT.indexOf(PASSAGE);

function answer(over: Partial<Message> = {}): Message {
  return assistantMessage({
    content: {
      sentences: [
        { text: "Revenue grew 12% in 2025.", citations: [C1] },
        { text: "The board approved the budget.", citations: [C1, C2] },
      ],
    },
    sources_used: [
      { source_id: "src_report", title: "Annual report.pdf" },
      { source_id: "src_notes", title: "Board notes" },
    ],
    ...over,
  });
}

async function ask(question: string) {
  const user = userEvent.setup();
  const box = await screen.findByRole("textbox", { name: "Question" });
  await waitFor(() => expect(box).toBeEnabled());
  await user.type(box, question);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  return user;
}

describe("chat", () => {
  it("streams stages and sentences with citations, then shows the stored answer", async () => {
    const sse = controlledSse();
    const { state, persist } = spaceBackend({
      sources: READY_SOURCES,
      stream: () => sse.response(),
    });
    renderApp(SPACE);

    expect(await screen.findByText("Using 1 of 2 sources")).toBeInTheDocument();
    const user = await ask("How did revenue develop?");

    // A conversation is created and titled from the question.
    await waitFor(() =>
      expect(state.calls.conversationsCreated).toEqual([{ title: "How did revenue develop?" }]),
    );
    await waitFor(() =>
      expect(state.calls.asked).toEqual([
        { conversationId: "cv_new1", content: "How did revenue develop?" },
      ]),
    );
    expect(screen.getByText("How did revenue develop?", { selector: "div" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Question" })).toHaveValue("");

    sse.push(sseEvent("status", { stage: "retrieving" }));
    expect(await screen.findByText("Searching your sources…")).toBeInTheDocument();
    sse.push(sseEvent("status", { stage: "generating" }));
    expect(await screen.findByText("Writing…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();

    sse.push(
      sseEvent("sentence", { index: 0, text: "Revenue grew 12% in 2025.", citations: [C1] }),
    );
    expect(await screen.findByText("Revenue grew 12% in 2025.")).toBeInTheDocument();
    expect(screen.getByText("Writing…")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Citation 1: Annual report.pdf, Pages 3–4" }),
    ).toBeInTheDocument();

    sse.push(sseEvent("status", { stage: "checking" }));
    sse.push(
      sseEvent("sentence", {
        index: 1,
        text: "The board approved the budget.",
        citations: [C1, C2],
      }),
    );
    expect(await screen.findByText("Checking…")).toBeInTheDocument();
    expect(await screen.findByText("The board approved the budget.")).toBeInTheDocument();

    const stored = answer({ id: "m_a1" });
    persist("cv_new1", [userMessage("How did revenue develop?", "m_u1"), stored]);
    sse.push(sseEvent("done", { message: stored }));
    sse.close();

    await waitFor(() => expect(screen.queryByText("Checking…")).not.toBeInTheDocument());
    const answers = screen.getAllByTestId("assistant-message");
    expect(answers).toHaveLength(1);
    const a = within(answers[0] as HTMLElement);
    expect(a.getAllByTestId("answer-sentence").map((s) => s.textContent)).toEqual([
      "Revenue grew 12% in 2025. 1 ",
      "The board approved the budget. 12",
    ]);
    // Phase 1 answers have no badge.
    expect(a.queryByTestId("answer-badge")).not.toBeInTheDocument();
    // The question is shown once, also after the refetch.
    await waitFor(() =>
      expect(screen.getAllByText("How did revenue develop?", { selector: "div" })).toHaveLength(1),
    );

    await user.click(a.getByRole("button", { name: /show sources used \(2\)/i }));
    const used = a.getByRole("list", { name: "Sources used" });
    expect(within(used).getByText("Annual report.pdf")).toBeInTheDocument();
    expect(within(used).getByText("Board notes")).toBeInTheDocument();
    expect(
      within(used).getByRole("button", { name: "Citation 2: Board notes, Section: Budget" }),
    ).toBeInTheDocument();
  });

  it("shows a calm refusal card with a suggestion and a Refused badge", async () => {
    const refused = assistantMessage({
      id: "m_r",
      badge: "refused",
      content: {
        text: "Your sources don't cover this.",
        suggestion: "Add sources about office leases.",
      },
    });
    const { persist } = spaceBackend({
      sources: READY_SOURCES,
      stream: ({ conversationId, content }) => {
        persist(conversationId, [userMessage(content, "m_u"), refused]);
        return sseResponse([
          sseEvent("status", { stage: "retrieving" }),
          sseEvent("refused", {
            reason: "not_in_sources",
            message: "Your sources don't cover this.",
            suggestion: "Add sources about office leases.",
          }),
          sseEvent("done", { message: refused }),
        ]);
      },
    });
    renderApp(SPACE);
    await ask("What does the office lease cost?");

    const card = await screen.findByTestId("refusal-card");
    expect(within(card).getByText("Not in your sources")).toBeInTheDocument();
    expect(within(card).getByText("Your sources don't cover this.")).toBeInTheDocument();
    expect(within(card).getByText("Add sources about office leases.")).toBeInTheDocument();
    expect(screen.getByTestId("answer-badge")).toHaveTextContent("Refused");
    expect(screen.queryByRole("button", { name: /show sources used/i })).not.toBeInTheDocument();
  });

  it("keeps a refusal visible when the stream ends without a stored message", async () => {
    spaceBackend({
      sources: READY_SOURCES,
      stream: () =>
        sseResponse([
          sseEvent("refused", {
            reason: "no_sources",
            message: "No ticked sources.",
            suggestion: null,
          }),
        ]),
    });
    renderApp(SPACE);
    await ask("Anything?");
    const card = await screen.findByTestId("refusal-card");
    expect(within(card).getByText("No ticked sources.")).toBeInTheDocument();
    // Still there after the messages refetch.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByTestId("refusal-card")).toBeInTheDocument();
  });

  it("shows stream errors with a way to try again", async () => {
    let calls = 0;
    spaceBackend({
      sources: READY_SOURCES,
      stream: () => {
        calls++;
        return sseResponse([sseEvent("error", { message: "The chat model did not answer." })]);
      },
    });
    renderApp(SPACE);
    const user = await ask("Why?");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The chat model did not answer.");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(calls).toBe(2));
  });

  it("opens the source viewer at the highlighted passage when a citation is clicked", async () => {
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");
    spaceBackend({
      sources: READY_SOURCES,
      conversations: [conversation()],
      messages: { cv_1: [userMessage("How did revenue develop?"), answer()] },
      chunks: {
        ch_1: chunk({
          id: "ch_1",
          text: PASSAGE,
          start_offset: PASSAGE_START,
          end_offset: PASSAGE_START + PASSAGE.length,
          locator: { pages: [3, 4] },
        }),
      },
      snapshots: {
        src_report: {
          markdown: SNAPSHOT_TEXT,
          map: { version: 1, kind: "pages", segments: [] },
        },
      },
    });
    const user = userEvent.setup();
    renderApp(SPACE);

    const [first] = await screen.findAllByRole("button", {
      name: "Citation 1: Annual report.pdf, Pages 3–4",
    });
    await user.click(first as HTMLElement);

    const viewer = await screen.findByRole("dialog", { name: "Annual report.pdf" });
    expect(within(viewer).getByText("Citation [1]")).toBeInTheDocument();
    expect(within(viewer).getByText("· Pages 3–4")).toBeInTheDocument();
    const mark = await within(viewer).findByTestId("cited-passage");
    expect(mark.tagName).toBe("MARK");
    expect(mark).toHaveTextContent(PASSAGE, { normalizeWhitespace: false });
    expect(within(viewer).getByTestId("snapshot-text")).toHaveTextContent(
      /Introduction\..*Outlook follows\./s,
    );
    expect(scrollSpy.mock.contexts).toContain(mark);
    expect(within(viewer).getByRole("link", { name: /open the original file/i })).toHaveAttribute(
      "href",
      "/api/spaces/sp_1/sources/src_report/original",
    );
  });

  it("lists conversations, switches between them and deletes one", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({
      sources: READY_SOURCES,
      conversations: [conversation(), conversation({ id: "cv_2", title: "Older chat" })],
      messages: {
        cv_1: [userMessage("Newest question")],
        cv_2: [userMessage("Older question", "m_o")],
      },
    });
    renderApp(SPACE);

    const select = await screen.findByRole("combobox", { name: "Conversation" });
    expect(await screen.findByText("Newest question")).toBeInTheDocument();
    await user.selectOptions(select, "cv_2");
    expect(await screen.findByText("Older question")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete conversation" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Delete conversation",
      }),
    );
    await waitFor(() => expect(state.calls.conversationsDeleted).toEqual(["cv_2"]));
    // Back to a new, empty conversation.
    expect(await screen.findByRole("option", { name: "New conversation" })).toBeInTheDocument();
  });
});

describe("chat box", () => {
  it("says to add a source when the space is empty", async () => {
    spaceBackend();
    renderApp(SPACE);
    expect(await screen.findByText("Add a source to start")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Question" })).toBeDisabled();
  });

  it("says to tick a source when none of the ready ones is ticked", async () => {
    spaceBackend({ sources: [source({ id: "a", enabled: false })] });
    renderApp(SPACE);
    expect(await screen.findByText("Tick at least one source to ask.")).toBeInTheDocument();
  });

  it("is greyed out with 'Set up in Settings' when chat is not set up", async () => {
    spaceBackend({ sources: READY_SOURCES, chatReady: false });
    renderApp(SPACE);
    const link = await screen.findByRole("link", { name: /set up in settings/i });
    expect(link).toHaveAttribute("href", "/settings#capability-chat");
    expect(document.querySelector('[data-capability-gate="chat"]')).not.toBeNull();
  });
});

describe("space overview", () => {
  it("shows the summary with citations and asks a suggested question on click", async () => {
    const { state } = spaceBackend({
      sources: READY_SOURCES,
      overview: overview({
        status: "ready",
        sentences: [{ text: "The space covers Acme's 2025 results.", citations: [C1] }],
        questions: ["How did revenue develop?", "What are the risks?", "Who approved the budget?"],
        generated_at: "2026-10-10T08:00:00Z",
      }),
      stream: () => sseResponse([sseEvent("status", { stage: "retrieving" })]),
    });
    const user = userEvent.setup();
    renderApp(SPACE);

    const card = await screen.findByTestId("overview-card");
    expect(within(card).getByText("The space covers Acme's 2025 results.")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Citation 1/ })).toBeInTheDocument();
    const questions = within(card).getAllByRole("button", { name: /\?$/ });
    expect(questions.map((b) => b.textContent)).toEqual([
      "How did revenue develop?",
      "What are the risks?",
      "Who approved the budget?",
    ]);

    await waitFor(() => expect(questions[1]).toBeEnabled());
    await user.click(questions[1] as HTMLElement);
    await waitFor(() =>
      expect(state.calls.asked).toEqual([
        { conversationId: "cv_new1", content: "What are the risks?" },
      ]),
    );
    // Once the conversation has content the overview steps aside.
    await waitFor(() => expect(screen.queryByTestId("overview-card")).not.toBeInTheDocument());
  });

  it("shows a skeleton while pending, and the token estimate before refreshing when remote", async () => {
    const { state } = spaceBackend({
      sources: READY_SOURCES,
      overview: overview({
        status: "ready",
        sentences: [{ text: "Old summary.", citations: [] }],
        questions: [],
        generated_at: "2026-10-09T08:00:00Z",
        stale: true,
        estimate: { tokens: 4200, remote: true },
      }),
    });
    const user = userEvent.setup();
    renderApp(SPACE);

    const card = await screen.findByTestId("overview-card");
    expect(within(card).getByText("Your sources changed since then.")).toBeInTheDocument();
    expect(
      within(card).getByText("About 4,200 tokens will be sent to your chat endpoint."),
    ).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(state.calls.overviewRefresh).toBe(1));
    expect(
      await within(card).findByText("Writing an overview of your sources…"),
    ).toBeInTheDocument();
    expect(within(card).queryByText("Old summary.")).not.toBeInTheDocument();
  });

  it("turns the overview off and on", async () => {
    const { state } = spaceBackend({
      sources: READY_SOURCES,
      overview: overview({ status: "ready", sentences: [{ text: "Summary.", citations: [] }] }),
    });
    const user = userEvent.setup();
    renderApp(SPACE);

    const toggle = await screen.findByRole("switch", { name: "Show space overview" });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(state.calls.overviewPut).toEqual([{ enabled: false }]);
    expect(screen.queryByText("Summary.")).not.toBeInTheDocument();
  });
});

describe("grounding hint", () => {
  const HINT = /Answers only come from your ticked sources/;

  it("is dismissed for good", async () => {
    const { state } = spaceBackend({ sources: READY_SOURCES });
    const user = userEvent.setup();
    renderApp(SPACE);
    const hint = (await screen.findByText(HINT)).closest('[role="note"]') as HTMLElement;
    expect(hint).toHaveTextContent("Ideas and brainstorming live in the Ideation tab.");
    await user.click(within(hint).getByRole("button", { name: "Dismiss hint" }));
    await waitFor(() => expect(screen.queryByText(HINT)).not.toBeInTheDocument());
    expect(state.calls.settings).toEqual([{ dismiss_hint: "chat_grounding" }]);
  });

  it("stays hidden once dismissed", async () => {
    spaceBackend({ sources: READY_SOURCES, settings: { hints_dismissed: ["chat_grounding"] } });
    renderApp(SPACE);
    await screen.findByText("Using 1 of 2 sources");
    expect(screen.queryByText(HINT)).not.toBeInTheDocument();
  });
});
