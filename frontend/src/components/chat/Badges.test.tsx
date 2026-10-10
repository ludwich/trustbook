import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Message } from "@/api/types";
import { assistantMessage, citation, conversation, source, userMessage } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { spaceBackend } from "@/test/spaceBackend";
import { controlledSse, sseEvent } from "@/test/sse";

const SPACE = "/spaces/sp_1";
const C1 = citation({ n: 1 });
const SOURCES = [source({ id: "src_report", title: "Annual report.pdf" })];

function answer(id: string, over: Partial<Message> = {}): Message {
  return assistantMessage({
    id,
    content: { sentences: [{ text: `Revenue grew 12% (${id}).`, citations: [C1] }] },
    sources_used: [{ source_id: "src_report", title: "Annual report.pdf" }],
    ...over,
  });
}

/** One conversation holding the given answers, each after its own question. */
function withAnswers(...answers: Message[]) {
  const messages = answers.flatMap((a, i) => [userMessage(`Question ${i + 1}`, `u${i}`), a]);
  spaceBackend({
    sources: SOURCES,
    conversations: [conversation({ id: "cv_1" })],
    messages: { cv_1: messages },
  });
  renderApp(SPACE);
}

async function answerCard(id: string) {
  const text = await screen.findByText(`Revenue grew 12% (${id}).`);
  return within(text.closest("article") as HTMLElement);
}

describe("answer badges", () => {
  it("shows Verified with a check, and no removed statements", async () => {
    withAnswers(answer("a1", { badge: "verified" }));
    const a = await answerCard("a1");
    const badge = a.getByTestId("answer-badge");
    expect(badge).toHaveTextContent(/^Verified$/);
    expect(badge).toHaveAttribute("data-badge", "verified");
    expect(badge.querySelector("svg")).not.toBeNull();
    expect(a.queryByTestId("removed-sentences")).not.toBeInTheDocument();
  });

  it("shows Partially verified with a count and a collapsed drawer of removed statements", async () => {
    const user = userEvent.setup();
    withAnswers(
      answer("a1", {
        badge: "partially_verified",
        removed_sentences: ["Profit doubled in 2025.", "The CEO resigned."],
      }),
    );
    const a = await answerCard("a1");
    expect(a.getByTestId("answer-badge")).toHaveTextContent(
      "Partially verified · 2 statements removed",
    );

    const drawer = a.getByRole("region", { name: "Unverified, not from your sources" });
    const toggle = within(drawer).getByRole("button", {
      name: /Unverified, not from your sources/,
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(drawer).queryByText("Profit doubled in 2025.")).not.toBeInTheDocument();
    // Removed statements are never part of the answer text.
    expect(a.getAllByTestId("answer-sentence").map((s) => s.textContent)).toEqual([
      "Revenue grew 12% (a1). 1",
    ]);

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const list = within(drawer).getByRole("list", { name: "Removed statements" });
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["Profit doubled in 2025.", "The CEO resigned."]);

    await user.click(toggle);
    expect(within(drawer).queryByRole("list")).not.toBeInTheDocument();
  });

  it("uses the singular for one removed statement", async () => {
    withAnswers(answer("a1", { badge: "partially_verified", removed_sentences: ["Only one."] }));
    const a = await answerCard("a1");
    expect(a.getByTestId("answer-badge")).toHaveTextContent("1 statement removed");
  });

  it("shows Unverified in grey with a tooltip saying checking is off", async () => {
    const user = userEvent.setup();
    withAnswers(answer("a1", { badge: "unverified" }));
    const a = await answerCard("a1");
    const badge = a.getByTestId("answer-badge");
    expect(badge).toHaveTextContent(/^Unverified$/);
    expect(badge.className).toContain("bg-muted");
    expect(badge).toHaveAccessibleDescription("Answer checking is turned off in Settings");

    // Reachable by keyboard, and the tooltip is tied to the badge.
    await user.tab();
    while (document.activeElement !== badge && document.activeElement !== document.body) {
      await user.tab();
    }
    expect(badge).toHaveFocus();
    expect(a.getByRole("tooltip")).toHaveTextContent("Answer checking is turned off in Settings");
  });

  it("points brainstorming questions to the Ideation tab", async () => {
    const user = userEvent.setup();
    withAnswers(
      answer("a1", { badge: "verified", content: { ...answer("a1").content, hint: "ideation" } }),
      answer("a2", { badge: "verified" }),
    );
    const a1 = await answerCard("a1");
    expect(a1.getByTestId("ideation-hint")).toHaveTextContent(
      "Brainstorming? The Ideation tab is made for that.",
    );
    const a2 = await answerCard("a2");
    expect(a2.queryByTestId("ideation-hint")).not.toBeInTheDocument();

    await user.click(a1.getByRole("button", { name: "Open Ideation" }));
    expect(screen.getByRole("tab", { name: "Ideation" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows a reply without sentences (the gate's greeting reply) as plain text", async () => {
    spaceBackend({
      sources: SOURCES,
      conversations: [conversation({ id: "cv_1" })],
      messages: {
        cv_1: [
          userMessage("hello"),
          assistantMessage({ content: { text: "I answer questions from your sources." } }),
        ],
      },
    });
    renderApp(SPACE);
    const reply = await screen.findByText("I answer questions from your sources.");
    const a = within(reply.closest("article") as HTMLElement);
    expect(a.queryByRole("button", { name: /citation/i })).not.toBeInTheDocument();
    expect(a.queryByRole("button", { name: /sources used/i })).not.toBeInTheDocument();
    expect(a.queryByTestId("answer-badge")).not.toBeInTheDocument();
  });

  it("says it is checking the answer while sentences are verified", async () => {
    const sse = controlledSse();
    const { persist } = spaceBackend({ sources: SOURCES, stream: () => sse.response() });
    const user = userEvent.setup();
    renderApp(SPACE);

    const box = await screen.findByRole("textbox", { name: "Question" });
    await waitFor(() => expect(box).toBeEnabled());
    await user.type(box, "How did revenue develop?");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    sse.push(sseEvent("status", { stage: "checking" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Checking the answer against your sources…",
    );

    const stored = answer("a1", { badge: "verified" });
    persist("cv_new1", [userMessage("How did revenue develop?", "m_u1"), stored]);
    sse.push(sseEvent("sentence", { index: 0, text: "Revenue grew 12% (a1).", citations: [C1] }));
    sse.push(sseEvent("done", { message: stored }));
    sse.close();

    const a = await answerCard("a1");
    expect(await a.findByTestId("answer-badge")).toHaveTextContent("Verified");
    expect(screen.queryByText("Checking the answer against your sources…")).not.toBeInTheDocument();
  });
});
