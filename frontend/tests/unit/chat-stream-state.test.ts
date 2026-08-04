import { describe, expect, it } from "vitest";

import {
  appendAnswerText,
  appendReferenceText,
  resolveChatTerminalState,
} from "@/app/chat/chat-stream-state";
import type { ChatRunResponse } from "@/lib/api/types";

function buildResult(status: string): ChatRunResponse {
  return {
    status,
    thread_id: "thread-1",
    conversation_id: 1,
    route: "rag",
    route_reason: "需要检索",
    answer: "",
    citations: [],
    need_human_review: status === "interrupted",
    review_reason: status === "interrupted" ? "证据不足" : "",
    review_payload: null,
    docs_preview: "",
    retrieved_docs_preview_items: [],
    relevance_decision: status === "interrupted" ? "need_review" : "confident",
    retrieval_hit_count: 0,
    answer_used_fallback: false,
    node_trace: [],
  };
}

describe("chat stream state", () => {
  it("accumulates answer deltas and appends references after the answer", () => {
    const answer = appendAnswerText(appendAnswerText("第一段", "\n"), "第二段");
    expect(appendReferenceText(answer, [2, 4])).toBe(
      "第一段\n第二段\n\n引用：[2][4]",
    );
  });

  it("keeps an interrupted result as pending human review", () => {
    const result = buildResult("interrupted");
    expect(resolveChatTerminalState(result)).toEqual({
      status: "interrupted",
      pendingReview: result,
    });
  });

  it("clears pending review after a completed resume", () => {
    expect(resolveChatTerminalState(buildResult("completed"))).toEqual({
      status: "completed",
      pendingReview: null,
    });
  });
});
