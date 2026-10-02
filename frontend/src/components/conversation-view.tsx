"use client";

import { useEffect, useState } from "react";
import { ApiError, requestJson, type ConversationDetail } from "../lib/api";
import { ChatTurn } from "./chat-turn";

export function ConversationView({ conversationId }: { conversationId: string }) {
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let refreshing = false;
    async function refresh() {
      if (refreshing || controller.signal.aborted) return;
      clearTimeout(timer);
      refreshing = true;
      let retry = true;
      try {
        const latest = await requestJson<ConversationDetail>(
          `/api/conversations/${encodeURIComponent(conversationId)}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setConversation(latest);
        setError(null);
        retry = latest.status !== "succeeded" && latest.status !== "failed";
      } catch (cause) {
        if (controller.signal.aborted) return;
        const permanent =
          cause instanceof ApiError &&
          cause.status >= 400 &&
          cause.status < 500 &&
          cause.status !== 408 &&
          cause.status !== 429;
        const message = cause instanceof ApiError ? cause.message : "Could not refresh this chat.";
        setError(permanent ? message : `${message} Retrying…`);
        retry = !permanent;
      } finally {
        refreshing = false;
      }
      if (retry) timer = setTimeout(refresh, 2_000);
    }
    void refresh();
    window.addEventListener("focus", refresh);
    return () => {
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [conversationId]);

  return (
    <div className="space-y-12">
      {error && (
        <p role="alert" aria-label="Chat update error">
          {error}
        </p>
      )}
      {!conversation && !error && <p role="status">Loading chat…</p>}
      {conversation?.runs.map((run) => (
        <ChatTurn key={run.id} run={run} />
      ))}
    </div>
  );
}
