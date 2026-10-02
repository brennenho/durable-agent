"use client";

import { useId, type FormEvent } from "react";
import { ArrowUp, LoaderCircle } from "lucide-react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

export function MessageComposer({
  label,
  prompt,
  onPromptChange,
  onSubmit,
  busy,
  error,
  disabled = false,
  placeholder = "Ask the agent to write, edit, or build something…",
  hint = "Enter to send · Shift + Enter for a new line",
}: {
  label: string;
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  busy: boolean;
  error: string | null;
  disabled?: boolean;
  placeholder?: string;
  hint?: string;
}) {
  const id = useId();
  return (
    <form onSubmit={onSubmit} aria-busy={busy} aria-label={label} className="chat-composer">
      <label htmlFor={id} className="sr-only">
        Message
      </label>
      <Textarea
        className="min-h-24 max-h-64 resize-y border-0 bg-transparent p-0 text-base shadow-none focus-visible:ring-0 md:text-sm"
        id={id}
        name="prompt"
        rows={3}
        required
        disabled={busy || disabled}
        value={prompt}
        onChange={(event) => onPromptChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
        aria-describedby={error ? `${id}-error` : `${id}-help`}
        placeholder={placeholder}
      />
      {error && (
        <p id={`${id}-error`} role="alert">
          {error}
        </p>
      )}
      <div className="mt-3 flex items-center justify-between gap-4">
        <p id={`${id}-help`} className="m-0 text-xs text-muted-foreground">
          {hint}
        </p>
        <Button
          type="submit"
          size="icon-sm"
          disabled={busy || disabled || !prompt.trim()}
          aria-label={busy ? "Sending…" : error ? "Try again" : "Send message"}
        >
          {busy ? (
            <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" />
          ) : (
            <ArrowUp aria-hidden="true" />
          )}
        </Button>
      </div>
    </form>
  );
}
