import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RefusalCard } from "./AnswerParts";

describe("RefusalCard (G5)", () => {
  it("shows the title once and the suggestion as the only subtitle", () => {
    render(
      <RefusalCard
        message="Not in your sources"
        suggestion="Add sources that cover “how to set a base URL for Session calls”, then ask again."
      />,
    );
    expect(screen.getAllByText("Not in your sources")).toHaveLength(1);
    expect(screen.getByTestId("refusal-suggestion")).toHaveTextContent(
      "Add sources that cover “how to set a base URL for Session calls”, then ask again.",
    );
  });

  it("shows no subtitle when the suggestion is null or empty", () => {
    const { rerender } = render(<RefusalCard message="Not in your sources" suggestion={null} />);
    expect(screen.queryByTestId("refusal-suggestion")).not.toBeInTheDocument();
    rerender(<RefusalCard message="Not in your sources" suggestion="   " />);
    expect(screen.queryByTestId("refusal-suggestion")).not.toBeInTheDocument();
  });

  it("never repeats the title as the subtitle", () => {
    render(<RefusalCard message="Not in your sources" suggestion="Not in your sources." />);
    expect(screen.getAllByText(/Not in your sources/)).toHaveLength(1);
    expect(screen.queryByTestId("refusal-suggestion")).not.toBeInTheDocument();
  });

  it("gives a verification refusal its own icon and fallback title (D-118)", () => {
    const { container, rerender } = render(
      <RefusalCard
        message="Couldn't verify an answer"
        suggestion="Try rephrasing the question, or name the file or function you mean."
        reason="unsupported"
      />,
    );
    const card = screen.getByTestId("refusal-card");
    expect(card).toHaveAttribute("data-reason", "unsupported");
    expect(screen.getByTestId("refusal-title")).toHaveTextContent("Couldn't verify an answer");
    expect(container.querySelector(".tabler-icon-shield-x")).not.toBeNull();
    expect(container.querySelector(".tabler-icon-file-search")).toBeNull();
    rerender(<RefusalCard message={null} suggestion={null} reason="unsupported" />);
    expect(screen.getByTestId("refusal-title")).toHaveTextContent("Couldn't verify an answer");
    // not_in_sources keeps its card unchanged.
    rerender(
      <RefusalCard message="Not in your sources" suggestion="Add." reason="not_in_sources" />,
    );
    expect(screen.getByTestId("refusal-title")).toHaveTextContent("Not in your sources");
    expect(container.querySelector(".tabler-icon-file-search")).not.toBeNull();
  });

  it("falls back to “Not in your sources” without a message, and keeps other titles", () => {
    const { rerender } = render(<RefusalCard message={null} suggestion={null} />);
    expect(screen.getByTestId("refusal-title")).toHaveTextContent("Not in your sources");
    rerender(<RefusalCard message="No answer with citations" suggestion="Ask again." />);
    expect(screen.getByTestId("refusal-title")).toHaveTextContent("No answer with citations");
    expect(screen.getByTestId("refusal-suggestion")).toHaveTextContent("Ask again.");
  });
});
