"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ApiError, requestJson, type ConversationDetail, type RunDetail } from "../lib/api";
import { ChatTurn } from "./chat-turn";
import { ChatWorkspace } from "./chat-workspace";
import { FollowUp } from "./follow-up";
import { InheritedHistory } from "./inherited-history";

export function ConversationView({ conversationId }: { conversationId: string }) {
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [revision, setRevision] = useState(0);
  const [awaitingRefresh, setAwaitingRefresh] = useState(false);
  const latestTurn = useRef<HTMLDivElement | null>(null);
  const scrollOnRefresh = useRef(false);
  const polling = useRef<AbortController | null>(null);

  function updateRun(saved: RunDetail) {
    // Prevent a read started before the action from replacing its confirmed state.
    polling.current?.abort();
    setConversation((current) => {
      if (!current) return current;
      const runs = current.runs.map((run) => (run.id === saved.id ? saved : run));
      const latest = runs[runs.length - 1];
      return { ...current, runs, status: latest.status, pause_requested: latest.pause_requested };
    });
    setError(null);
    setRevision((value) => value + 1);
  }

  useEffect(() => {
    if (conversation && scrollOnRefresh.current) {
      latestTurn.current?.scrollIntoView({ block: "start" });
      scrollOnRefresh.current = false;
    }
  }, [conversation]);

  function requestRefresh() {
    scrollOnRefresh.current = true;
    setAwaitingRefresh(true);
    setRevision((value) => value + 1);
  }

  useEffect(() => {
    const controller = new AbortController();
    polling.current = controller;
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
        setAwaitingRefresh(false);
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
  }, [conversationId, revision]);

  return (
    <ChatWorkspace
      runs={conversation?.runs ?? []}
      composer={
        conversation && (
          <FollowUp
            conversation={conversation}
            blocked={awaitingRefresh || error !== null}
            refresh={requestRefresh}
          />
        )
      }
    >
      <div className="space-y-12">
        {conversation?.forked_from && (
          <div className="space-y-8">
            <InheritedHistory key={conversation.runs[0].id} run={conversation.runs[0]} />
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-4 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Branched here</span>
              <Link
                href={`/chats/${encodeURIComponent(conversation.forked_from.conversation_id)}`}
                className="underline underline-offset-4"
              >
                Branched from step {conversation.forked_from.position + 1} in the source chat
              </Link>
            </div>
          </div>
        )}
        {error && (
          <p role="alert" aria-label="Chat update error">
            {error}
          </p>
        )}
        {!conversation && !error && <p role="status">Loading chat…</p>}
        {conversation?.runs.map((run, index) => (
          <div key={run.id} ref={index === conversation.runs.length - 1 ? latestTurn : undefined}>
            <ChatTurn run={run} onUpdate={updateRun} live={!error} />
          </div>
        ))}
      </div>
    </ChatWorkspace>
  );
}
