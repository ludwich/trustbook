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

  it("falls back to “Not in your sources” without a message, and keeps other titles", () => {
    const { rerender } = render(<RefusalCard message={null} suggestion={null} />);
    expect(screen.getByTestId("refusal-title")).toHaveTextContent("Not in your sources");
    rerender(<RefusalCard message="No answer with citations" suggestion="Ask again." />);
    expect(screen.getByTestId("refusal-title")).toHaveTextContent("No answer with citations");
    expect(screen.getByTestId("refusal-suggestion")).toHaveTextContent("Ask again.");
  });
});
