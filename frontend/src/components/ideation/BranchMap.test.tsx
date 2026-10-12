import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ideationNode, ideationSession } from "@/test/fixtures";
import { BranchMap } from "./BranchMap";

function session() {
  return ideationSession({
    nodes: [
      ideationNode({
        id: "n1",
        title: "Who it is for",
        status: "resolved",
        question: "Who is it for?",
        final_answer: "Families",
        answer_source: "user",
        dismissible: false,
      }),
      ideationNode({
        id: "n1a",
        parent_id: "n1",
        depth: 1,
        origin: "answer",
        title: "Follow-up on families",
      }),
      ideationNode({
        id: "n2",
        ordinal: 1,
        title: "What your sources say about puffins",
        status: "open",
        question: "What do the sources say?",
        dismissible: false,
      }),
      ideationNode({ id: "n3", ordinal: 2, title: "Risks and open problems" }),
      ideationNode({ id: "n4", ordinal: 3, title: "Pricing", status: "dismissed" }),
      ideationNode({
        id: "n5",
        ordinal: 4,
        title: "Weather",
        status: "resolved",
        question: "What about the weather?",
        answer_source: "skipped",
        dismissible: false,
      }),
    ],
    current_node_id: "n2",
    risks: [{ text: "Raised in an answer: storms", node_id: "n1" }],
  });
}

function nodeEl(title: string): HTMLElement {
  const el = screen.getByText(title).closest("[data-testid='map-node']");
  if (!(el instanceof HTMLElement)) throw new Error(`no map node "${title}"`);
  return el;
}

describe("BranchMap", () => {
  it('shows "X resolved, Y open" and each branch in its state', () => {
    render(<BranchMap session={session()} canDismiss onDismiss={() => {}} />);

    const map = screen.getByRole("region", { name: "Branch map" });
    expect(within(map).getByTestId("map-progress")).toHaveTextContent("2 resolved, 3 open");
    expect(within(map).queryByTestId("map-complete")).not.toBeInTheDocument();

    // Tentative = dashed, resolved = solid, current highlighted.
    const tentative = nodeEl("Risks and open problems");
    expect(tentative).toHaveAttribute("data-status", "tentative");
    expect(tentative.className).toContain("border-dashed");
    expect(tentative).toHaveTextContent("(not asked yet)");

    const resolved = nodeEl("Who it is for");
    expect(resolved).toHaveAttribute("data-status", "resolved");
    expect(resolved.className).toContain("border-solid");
    expect(resolved.className).not.toContain("border-dashed");
    expect(resolved).toHaveTextContent("(answered)");
    expect(nodeEl("Weather")).toHaveTextContent("(skipped)");

    const current = nodeEl("What your sources say about puffins");
    expect(current).toHaveAttribute("data-current", "true");
    expect(current).toHaveAttribute("aria-current", "step");
    expect(current).toHaveTextContent("(current question)");
    expect(
      within(map)
        .getAllByTestId("map-node")
        .filter((n) => n.dataset.current),
    ).toHaveLength(1);

    // A branch an answer opened sits under its parent; dismissed branches are hidden.
    const under = screen.getByRole("list", { name: "Under “Who it is for”" });
    expect(within(under).getByText("Follow-up on families")).toBeInTheDocument();
    expect(screen.queryByText("Pricing")).not.toBeInTheDocument();

    // Risks are plain text, never grounded.
    expect(screen.getByRole("list", { name: "Risks raised" })).toHaveTextContent(
      "Raised in an answer: storms",
    );
  });

  it("offers × only on dismissible branches and reports which one", async () => {
    const onDismiss = vi.fn();
    const user = userEvent.setup();
    render(<BranchMap session={session()} canDismiss onDismiss={onDismiss} />);

    const buttons = screen.getAllByRole("button", { name: /^Dismiss branch/ });
    expect(buttons.map((b) => b.getAttribute("aria-label"))).toEqual([
      "Dismiss branch “Follow-up on families”",
      "Dismiss branch “Risks and open problems”",
    ]);
    await user.click(
      screen.getByRole("button", { name: "Dismiss branch “Risks and open problems”" }),
    );
    expect(onDismiss).toHaveBeenCalledWith(expect.objectContaining({ id: "n3" }));
  });

  it("hides × while dismissing is not possible (turn running or finished)", () => {
    render(<BranchMap session={session()} canDismiss={false} onDismiss={() => {}} />);
    expect(screen.queryByRole("button", { name: /^Dismiss branch/ })).not.toBeInTheDocument();
  });

  it("shows Complete when no branch is open", () => {
    const s = ideationSession({
      nodes: [
        ideationNode({
          id: "n1",
          status: "resolved",
          question: "Q?",
          answer_source: "accepted",
          dismissible: false,
        }),
        ideationNode({ id: "n2", status: "dismissed" }),
      ],
    });
    render(<BranchMap session={s} canDismiss onDismiss={() => {}} />);
    expect(screen.getByTestId("map-complete")).toHaveTextContent("Complete");
    expect(screen.getByTestId("map-progress")).toHaveTextContent("1 resolved, 0 open");
  });

  it("explains the empty map before the first question", () => {
    render(<BranchMap session={ideationSession()} canDismiss onDismiss={() => {}} />);
    expect(screen.getByTestId("map-progress")).toHaveTextContent("0 resolved, 0 open");
    expect(screen.getByText("The first branches appear with the first question.")).toBeVisible();
    expect(screen.queryByTestId("map-complete")).not.toBeInTheDocument();
  });
});
