import { IconPlayerStopFilled, IconSend } from "@tabler/icons-react";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router";
import type { ChatStatus } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { askBlocker } from "./askBlocker";

export function Composer({
  status,
  running,
  onAsk,
  onStop,
}: {
  status: ChatStatus | undefined;
  running: boolean;
  onAsk: (question: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const blocker = askBlocker(status);
  const disabled = blocker !== null;

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const q = text.trim();
    if (!q || disabled || running) return;
    onAsk(q);
    setText("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      onSubmit={submit}
      className="rounded-lg border bg-card p-2 shadow-xs focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30"
    >
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={disabled}
        rows={2}
        placeholder={disabled ? "" : "Ask about your sources"}
        aria-label="Question"
        aria-describedby="composer-note"
        className="max-h-48 min-h-14 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
      />
      <div className="flex items-center justify-between gap-3 px-1 pt-1">
        <span id="composer-note" className="text-xs text-muted-foreground">
          {blocker === "Set up in Settings" ? (
            <Link to="/settings#capability-chat" className="text-primary hover:underline">
              Set up in Settings
            </Link>
          ) : blocker && blocker !== "Loading…" ? (
            blocker
          ) : status ? (
            `Using ${status.enabled_sources} of ${status.ready_sources} ${status.ready_sources === 1 ? "source" : "sources"}`
          ) : (
            ""
          )}
        </span>
        {running ? (
          <Button size="sm" variant="outline" onClick={onStop}>
            <IconPlayerStopFilled aria-hidden />
            Stop
          </Button>
        ) : (
          <Button size="sm" type="submit" disabled={disabled || !text.trim()}>
            <IconSend aria-hidden />
            Ask
          </Button>
        )}
      </div>
    </form>
  );
}
