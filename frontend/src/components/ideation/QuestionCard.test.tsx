import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Recommendation } from "@/api/types";
import { CitationViewerContext } from "@/components/chat/useCitationViewer";
import { citation, ideationNode } from "@/test/fixtures";
import { NO_RECOMMENDATION, QuestionCard } from "./QuestionCard";

const C1 = citation({ n: 1, source_title: "Gull Island guide", locator: { pages: [1] } });
const CITED = "Gull Island has a colony of puffins that nest on the north cliffs.";
const SUGGESTED = "Start small and try it with one real user before building more.";
const REMOVED = "This also requires a licence key from the vendor.";

function recommendation(over: Partial<Recommendation> = {}): Recommendation {
  return {
    grounding: "partly_cited",
    badge: "partially_verified",
    sentences: [
      { text: CITED, citations: [C1], kind: "cited" },
      { text: SUGGESTED, citations: [], kind: "suggestion" },
    ],
    removed: [REMOVED],
    ...over,
  };
}

function node(rec: Recommendation | null = recommendation()) {
  return ideationNode({
    id: "n1",
    title: "Who it is for",
    status: "open",
    question: "Who should the walks be for?",
    recommendation: rec,
    dismissible: false,
  });
}

function withViewer(ui: ReactElement) {
  const open = vi.fn();
  render(<CitationViewerContext.Provider value={{ open }}>{ui}</CitationViewerContext.Provider>);
  return open;
}

describe("QuestionCard", () => {
  it("shows the question with cited sentences, tagged suggestions and the removed drawer", async () => {
    const user = userEvent.setup();
    const open = withViewer(<QuestionCard node={node()} canAnswer onAnswer={() => {}} />);

    const card = screen.getByRole("region", { name: "Who should the walks be for?" });
    expect(card).toHaveTextContent("Who it is for");

    const [cited, suggested] = within(card).getAllByTestId("recommendation-sentence");
    expect(cited).toHaveAttribute("data-kind", "cited");
    expect(cited).toHaveTextContent(CITED);
    expect(within(cited!).queryByTestId("suggestion-tag")).not.toBeInTheDocument();
    expect(suggested).toHaveAttribute("data-kind", "suggestion");
    expect(within(suggested!).getByTestId("suggestion-tag")).toHaveTextContent("Suggestion");
    // A suggestion never carries a citation number.
    expect(within(suggested!).queryByRole("button")).not.toBeInTheDocument();
    expect(card).toHaveTextContent("They are not from your sources.");

    // The citation opens the source viewer.
    await user.click(
      within(cited!).getByRole("button", { name: "Citation 1: Gull Island guide, Page 1" }),
    );
    expect(open).toHaveBeenCalledWith(C1);

    // Chat's badge, counting removed statements.
    expect(within(card).getByTestId("answer-badge")).toHaveAttribute(
      "data-badge",
      "partially_verified",
    );

    // Removed (unverified) sentences sit in the collapsed drawer, never as suggestions.
    const drawer = within(card).getByRole("region", { name: "Unverified, not from your sources" });
    expect(drawer).not.toHaveTextContent(REMOVED);
    await user.click(within(drawer).getByRole("button", { expanded: false }));
    expect(within(drawer).getByRole("list", { name: "Removed statements" })).toHaveTextContent(
      REMOVED,
    );
    expect(within(card).getByTestId("recommendation-text")).not.toHaveTextContent(REMOVED);
  });

  it("accepts, skips and sends a typed answer", async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    withViewer(<QuestionCard node={node()} canAnswer onAnswer={onAnswer} />);

    await user.click(screen.getByRole("button", { name: "Accept recommendation" }));
    expect(onAnswer).toHaveBeenLastCalledWith("accept");
    await user.click(screen.getByRole("button", { name: "Skip" }));
    expect(onAnswer).toHaveBeenLastCalledWith("skip");

    const send = screen.getByRole("button", { name: "Answer" });
    expect(send).toBeDisabled();
    const box = screen.getByRole("textbox", { name: "Your answer" });
    await user.type(box, "  Families with children  ");
    expect(send).toBeEnabled();
    await user.click(send);
    expect(onAnswer).toHaveBeenLastCalledWith("answer", "Families with children");

    // Enter sends too; Shift+Enter is a new line.
    await user.clear(box);
    await user.type(box, "Line one{Shift>}{Enter}{/Shift}line two{Enter}");
    expect(onAnswer).toHaveBeenLastCalledWith("answer", "Line one\nline two");
    expect(onAnswer).toHaveBeenCalledTimes(4);
  });

  it("disables Accept when there is no recommended answer", () => {
    withViewer(
      <QuestionCard
        node={node(recommendation({ sentences: [], removed: [REMOVED], badge: null }))}
        canAnswer
        onAnswer={() => {}}
      />,
    );
    expect(screen.getByText(NO_RECOMMENDATION)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept recommendation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Skip" })).toBeEnabled();
    expect(screen.queryByTestId("answer-badge")).not.toBeInTheDocument();
  });

  it("shows only suggestions when nothing is cited, without a badge", () => {
    withViewer(
      <QuestionCard
        node={node(
          recommendation({
            grounding: "suggestion",
            badge: null,
            removed: [],
            sentences: [{ text: SUGGESTED, citations: [], kind: "suggestion" }],
          }),
        )}
        canAnswer
        onAnswer={() => {}}
      />,
    );
    expect(screen.getAllByTestId("suggestion-tag")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /^Citation/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("answer-badge")).not.toBeInTheDocument();
    expect(screen.queryByTestId("removed-sentences")).not.toBeInTheDocument();
  });

  it("disables the actions while busy and hides them when answering is not possible", () => {
    const { unmount } = render(<QuestionCard node={node()} canAnswer busy onAnswer={() => {}} />);
    expect(screen.getByRole("button", { name: "Accept recommendation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Skip" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Your answer" })).toBeDisabled();
    unmount();

    render(<QuestionCard node={node()} canAnswer={false} onAnswer={() => {}} />);
    expect(screen.getByText("Who should the walks be for?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept recommendation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
