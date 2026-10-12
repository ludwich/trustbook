import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { IdeationNode, IdeationSession } from "@/api/types";
import {
  chunk,
  citation,
  ideationNode,
  ideationSession,
  source,
  withProgress,
} from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { spaceBackend } from "@/test/spaceBackend";
import { controlledSse, sseEvent, sseResponse } from "@/test/sse";
import { resetIdeationStreams } from "./useIdeationStream";

const SPACE = "/spaces/sp_1";
const IDEA = "Guided puffin walks on Gull Island for birdwatchers";
const C1 = citation({
  n: 1,
  chunk_id: "ch_p",
  source_title: "Gull Island guide",
  locator: { pages: [1] },
});
const CITED = "Gull Island has a colony of puffins that nest on the north cliffs.";
const SUGGESTED = "Start small and try it with one real user before building more.";

function asked(id: string, title: string, over: Partial<IdeationNode> = {}): IdeationNode {
  return ideationNode({
    id,
    title,
    status: "open",
    question: `What should be decided about “${title}”?`,
    recommendation: {
      grounding: "cited",
      badge: "verified",
      sentences: [{ text: CITED, citations: [C1], kind: "cited" }],
      removed: [],
    },
    asked_at: "2026-10-10T09:01:00Z",
    dismissible: false,
    ...over,
  });
}

/** The session after the first turn: three seeds, the first one asked. */
function firstTurn(id = "is_1"): IdeationSession {
  return ideationSession({
    id,
    nodes: [
      asked("n1", "Who it is for"),
      ideationNode({ id: "n2", ordinal: 1, title: "What your sources say about guided" }),
      ideationNode({ id: "n3", ordinal: 2, title: "Risks and open problems" }),
    ],
    current_node_id: "n1",
  });
}

/** The session after answering n1 and asking n2 (with a suggestion only). */
function secondTurn(source: "user" | "accepted" | "skipped", text: string | null) {
  const s = firstTurn();
  return withProgress({
    ...s,
    nodes: [
      {
        ...s.nodes[0]!,
        status: "resolved",
        answer_source: source,
        final_answer: text,
        answered_at: "2026-10-10T09:02:00Z",
      },
      asked("n2", "What your sources say about guided", {
        ordinal: 1,
        recommendation: {
          grounding: "suggestion",
          badge: null,
          sentences: [{ text: SUGGESTED, citations: [], kind: "suggestion" }],
          removed: [],
        },
      }),
      s.nodes[2]!,
    ],
    current_node_id: "n2",
  });
}

async function openIdeation() {
  const user = userEvent.setup();
  renderApp(SPACE);
  await user.click(await screen.findByRole("tab", { name: "Ideation" }));
  return user;
}

function mapNode(title: string): HTMLElement {
  const map = screen.getByRole("region", { name: "Branch map" });
  const el = within(map).getByText(title).closest("[data-testid='map-node']");
  if (!(el instanceof HTMLElement)) throw new Error(`no map node "${title}"`);
  return el;
}

beforeEach(() => resetIdeationStreams());

describe("Ideation tab", () => {
  it("starts an idea, shows the stages, then the first question and the seeded map", async () => {
    const sse = controlledSse();
    const { state, putSession } = spaceBackend({
      sources: [source({ id: "src_g", title: "Gull Island guide" })],
      ideationStream: () => sse.response(),
    });
    const user = await openIdeation();

    const box = await screen.findByRole("textbox", { name: "I have an idea" });
    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
    // The chat endpoint is local: no token estimate.
    expect(screen.queryByTestId("ideation-estimate")).not.toBeInTheDocument();
    await user.type(box, `  ${IDEA}  `);
    await user.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(state.ideationCalls.created).toEqual([{ idea: IDEA }]));
    await waitFor(() =>
      expect(state.ideationCalls.streams).toEqual([{ sessionId: "is_new1", answer: null }]),
    );
    expect(await screen.findByText("Sketching the first branches…")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Idea session" })).toHaveValue("is_new1");

    sse.push(sseEvent("status", { stage: "generating" }));
    sse.push(sseEvent("status", { stage: "retrieving" }));
    expect(await screen.findByText("Searching your sources…")).toBeInTheDocument();
    sse.push(sseEvent("status", { stage: "checking" }));
    expect(
      await screen.findByText("Checking the recommendation against your sources…"),
    ).toBeInTheDocument();

    const stored = firstTurn("is_new1");
    putSession(stored);
    sse.push(sseEvent("done", { session: stored }));
    sse.close();

    const card = await screen.findByRole("region", {
      name: "What should be decided about “Who it is for”?",
    });
    expect(within(card).getByText(CITED)).toBeInTheDocument();
    expect(within(card).getByTestId("answer-badge")).toHaveAttribute("data-badge", "verified");
    // The new question gets focus.
    await waitFor(() => expect(within(card).getByTestId("question-text")).toHaveFocus());
    expect(screen.getByTestId("map-progress")).toHaveTextContent("0 resolved, 3 open");
    expect(mapNode("Who it is for")).toHaveAttribute("data-current", "true");
    expect(mapNode("Risks and open problems")).toHaveAttribute("data-status", "tentative");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("resumes the newest session after a reload with the same question and map, without a model call", async () => {
    const resumed = secondTurn("accepted", CITED);
    const { state } = spaceBackend({
      ideationSessions: [resumed, ideationSession({ id: "is_old", title: "Older idea" })],
    });
    await openIdeation();

    expect(
      await screen.findByRole("region", {
        name: "What should be decided about “What your sources say about guided”?",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Idea session" })).toHaveValue("is_1");
    expect(screen.getByTestId("map-progress")).toHaveTextContent("1 resolved, 2 open");
    expect(mapNode("What your sources say about guided")).toHaveAttribute("data-current", "true");
    expect(mapNode("Who it is for")).toHaveAttribute("data-status", "resolved");

    // The thread shows the accepted recommendation, with its citation.
    const thread = screen.getByRole("list", { name: "Interview so far" });
    expect(within(thread).getByTestId("accepted-tag")).toHaveTextContent("Accepted recommendation");
    expect(
      within(thread).getByRole("button", { name: /^Citation 1: Gull Island guide/ }),
    ).toBeInTheDocument();

    // A suggestion-only recommendation: tagged, no badge.
    const card = screen.getByTestId("question-card");
    expect(within(card).getByTestId("suggestion-tag")).toBeInTheDocument();
    expect(within(card).queryByTestId("answer-badge")).not.toBeInTheDocument();
    expect(state.ideationCalls.streams).toEqual([]);
  });

  it("after a reload during a turn, waits for the server's turn instead of asking again", async () => {
    // The answer to n1 is stored; the server is still preparing the next question.
    const answered = secondTurn("user", "Birdwatchers from the mainland");
    const midTurn = withProgress({
      ...answered,
      nodes: answered.nodes.map((n) =>
        n.id === "n2" ? { ...n, status: "tentative", question: null, recommendation: null } : n,
      ),
      current_node_id: null,
      running: true,
    });
    const { state, putSession } = spaceBackend({ ideationSessions: [midTurn] });
    await openIdeation();

    expect(await screen.findByText("Preparing the next question…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask the next question" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("question-card")).not.toBeInTheDocument();
    // Nothing can be dismissed while the turn runs.
    const dismiss = { name: "Dismiss branch “Risks and open problems”" };
    expect(screen.queryByRole("button", dismiss)).not.toBeInTheDocument();

    // The server's turn ends: the polled session shows the next question.
    putSession(answered);
    expect(
      await screen.findByRole(
        "region",
        { name: "What should be decided about “What your sources say about guided”?" },
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Preparing the next question…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", dismiss)).toBeInTheDocument();
    expect(state.ideationCalls.streams).toEqual([]);
  });

  it("accepts, answers and skips: each sends the answer and shows the next question", async () => {
    let step = 0;
    const replies = [
      () => secondTurn("accepted", CITED),
      () => {
        const s = secondTurn("accepted", CITED);
        return withProgress({
          ...s,
          nodes: [
            s.nodes[0]!,
            {
              ...s.nodes[1]!,
              status: "resolved",
              answer_source: "user",
              final_answer: "Families",
              answered_at: "2026-10-10T09:03:00Z",
            },
            asked("n3", "Risks and open problems", { ordinal: 2 }),
          ],
          current_node_id: "n3",
        });
      },
    ];
    const { state, putSession } = spaceBackend({
      ideationSessions: [firstTurn()],
      ideationStream: () => {
        const next = replies[step++]!();
        putSession(next);
        return sseResponse([
          sseEvent("status", { stage: "retrieving" }),
          sseEvent("done", { session: next }),
        ]);
      },
    });
    const user = await openIdeation();

    await user.click(await screen.findByRole("button", { name: "Accept recommendation" }));
    await screen.findByRole("region", {
      name: "What should be decided about “What your sources say about guided”?",
    });
    await user.type(screen.getByRole("textbox", { name: "Your answer" }), "Families");
    await user.click(screen.getByRole("button", { name: "Answer" }));
    await screen.findByRole("region", {
      name: "What should be decided about “Risks and open problems”?",
    });
    expect(screen.getByTestId("map-progress")).toHaveTextContent("2 resolved, 1 open");
    const items = within(screen.getByRole("list", { name: "Interview so far" })).getAllByTestId(
      "thread-item",
    );
    expect(items.map((i) => i.dataset.answerSource)).toEqual(["accepted", "user"]);
    expect(items[1]).toHaveTextContent("Families");

    // Skip: the server replies 409 (e.g. answered elsewhere); the detail is shown as is.
    server.use(
      http.post("*/api/spaces/:sid/ideation/sessions/:id/answer", () =>
        HttpResponse.json({ detail: "This question was already answered." }, { status: 409 }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Skip" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This question was already answered.",
    );

    expect(state.ideationCalls.streams).toEqual([
      { sessionId: "is_1", answer: { node_id: "n1", action: "accept" } },
      { sessionId: "is_1", answer: { node_id: "n2", action: "answer", text: "Families" } },
    ]);
  });

  it('shows an error event with "Ask again", which asks for the next question', async () => {
    let calls = 0;
    const { state, putSession } = spaceBackend({
      ideationSessions: [firstTurn()],
      ideationStream: ({ answer }) => {
        calls++;
        if (answer) {
          // The answer is stored; the turn after it fails.
          const s = secondTurn("skipped", null);
          putSession({
            ...withProgress({
              ...s,
              nodes: [
                s.nodes[0]!,
                {
                  ...s.nodes[1]!,
                  status: "tentative",
                  question: null,
                  recommendation: null,
                  dismissible: true,
                },
                s.nodes[2]!,
              ],
            }),
            current_node_id: null,
          });
          return sseResponse([
            sseEvent("status", { stage: "generating" }),
            sseEvent("error", {
              message: "The chat model's reply could not be used. Press Ask again.",
            }),
          ]);
        }
        const s = secondTurn("skipped", null);
        putSession(s);
        return sseResponse([sseEvent("done", { session: s })]);
      },
    });
    const user = await openIdeation();

    await user.click(await screen.findByRole("button", { name: "Skip" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The chat model's reply could not be used. Press Ask again.");
    // The skip was kept.
    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Interview so far" })).getByText(/Skipped/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("question-card")).not.toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: "Ask again" }));
    await screen.findByRole("region", {
      name: "What should be decided about “What your sources say about guided”?",
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(calls).toBe(2);
    expect(state.ideationCalls.streams.at(-1)).toEqual({ sessionId: "is_1", answer: null });
  });

  it("shows 409s on a turn (chat not set up) as plain messages", async () => {
    spaceBackend({
      ideationSessions: [ideationSession({ id: "is_1" })],
    });
    server.use(
      http.post("*/api/spaces/:sid/ideation/sessions/:id/turn", () =>
        HttpResponse.json({ detail: "Set up the chat model in Settings first." }, { status: 409 }),
      ),
    );
    const user = await openIdeation();
    await user.click(await screen.findByRole("button", { name: "Start the interview" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Set up the chat model in Settings first.");
    expect(within(alert).getByRole("button", { name: "Ask again" })).toBeInTheDocument();
  });

  it("dismisses a seed from the map", async () => {
    const { state } = spaceBackend({ ideationSessions: [firstTurn()] });
    const user = await openIdeation();

    await user.click(
      await screen.findByRole("button", { name: "Dismiss branch “Risks and open problems”" }),
    );
    await waitFor(() => expect(state.ideationCalls.dismissed).toEqual(["n3"]));
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Branch map" })).queryByText(
          "Risks and open problems",
        ),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("map-progress")).toHaveTextContent("0 resolved, 2 open");
    // Asked branches have no ×.
    expect(
      screen.queryByRole("button", { name: "Dismiss branch “Who it is for”" }),
    ).not.toBeInTheDocument();
  });

  it("finishes mid-session, offers the Markdown export, and continues", async () => {
    const { state } = spaceBackend({ ideationSessions: [firstTurn()] });
    const user = await openIdeation();

    expect(await screen.findByTestId("question-card")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Export Markdown" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Finish now" }));

    const link = await screen.findByRole("link", { name: "Export Markdown" });
    expect(link).toHaveAttribute("href", "/api/spaces/sp_1/ideation/sessions/is_1/brief");
    expect(link).toHaveAttribute("download");
    expect(state.ideationCalls.finished).toEqual(["is_1"]);
    expect(screen.getByTestId("ideation-finished")).toHaveTextContent(
      "Press Continue to add to it.",
    );
    // The waiting question stays visible, without actions; no × on the map.
    expect(screen.getByTestId("question-card")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept recommendation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Dismiss branch/ })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Idea session" })).toHaveTextContent("(finished)");

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(
      await screen.findByRole("button", { name: "Accept recommendation" }),
    ).toBeInTheDocument();
    expect(state.ideationCalls.continued).toEqual(["is_1"]);
    // The earlier brief can still be exported.
    expect(screen.getByRole("link", { name: "Export Markdown" })).toBeInTheDocument();
  });

  it("shows Complete when every branch is resolved", async () => {
    const s = secondTurn("accepted", CITED);
    const done = withProgress({
      ...s,
      nodes: s.nodes.map((n, i) => ({
        ...n,
        status: "resolved" as const,
        question: n.question ?? "Q?",
        answer_source: "user" as const,
        final_answer: "Yes",
        answered_at: `2026-10-10T09:0${i + 2}:00Z`,
        dismissible: false,
      })),
      current_node_id: null,
    });
    spaceBackend({ ideationSessions: [done] });
    await openIdeation();
    expect(await screen.findByTestId("map-complete")).toHaveTextContent("Complete");
    expect(screen.getByTestId("ideation-complete")).toHaveTextContent("every branch is resolved");
    expect(screen.getAllByRole("button", { name: "Finish now" })).toHaveLength(2);
  });

  it("opens the source viewer from a recommendation's citation", async () => {
    spaceBackend({
      ideationSessions: [firstTurn()],
      contexts: {
        ch_p: {
          chunk: chunk({
            id: "ch_p",
            source_title: "Gull Island guide",
            text: CITED,
            locator: { pages: [1] },
          }),
          source_type: "paste",
          view: "text",
          text: { label: "Page 1", before: "", highlight: CITED, after: "" },
        },
      },
    });
    const user = await openIdeation();
    const card = await screen.findByTestId("question-card");
    await user.click(
      within(card).getByRole("button", { name: "Citation 1: Gull Island guide, Page 1" }),
    );
    const viewer = await screen.findByRole("dialog", { name: "Gull Island guide" });
    expect(await within(viewer).findByTestId("cited-passage")).toHaveTextContent(CITED);
  });

  it("shows the token estimate before a session when the chat endpoint is remote", async () => {
    spaceBackend({ ideationEstimate: { tokens_per_turn: 1800, seed_tokens: 400, remote: true } });
    await openIdeation();
    expect(await screen.findByTestId("ideation-estimate")).toHaveTextContent(
      "About 1,800 tokens per question are sent to your chat endpoint, plus about 400 once to start.",
    );
    expect(
      screen.getByText("No ticked, ready sources: every recommendation will be a suggestion."),
    ).toBeInTheDocument();
  });

  it("switches between sessions, starts a new idea and deletes one", async () => {
    const { state } = spaceBackend({
      ideationSessions: [
        firstTurn(),
        ideationSession({ id: "is_old", title: "Older idea", idea: "Older" }),
      ],
    });
    const user = await openIdeation();

    const select = await screen.findByRole("combobox", { name: "Idea session" });
    await screen.findByTestId("question-card");
    await user.selectOptions(select, "is_old");
    expect(await screen.findByRole("button", { name: "Start the interview" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Older idea" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New idea" }));
    expect(await screen.findByRole("textbox", { name: "I have an idea" })).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Idea session" }), "is_1");
    await user.click(await screen.findByRole("button", { name: "Delete idea session" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Delete “Puffin walks”?");
    await user.click(within(dialog).getByRole("button", { name: "Delete idea" }));
    await waitFor(() => expect(state.ideationCalls.deleted).toEqual(["is_1"]));
    expect(await screen.findByRole("textbox", { name: "I have an idea" })).toBeInTheDocument();
    expect(
      within(screen.getByRole("combobox", { name: "Idea session" })).queryByText("Puffin walks"),
    ).not.toBeInTheDocument();
  });
});
