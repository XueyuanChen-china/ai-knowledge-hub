import type { ChatRunResponse } from "@/lib/api/types";

export type ChatTerminalState = {
  status: "interrupted" | "completed";
  pendingReview: ChatRunResponse | null;
};

export function appendAnswerText(current: string, delta: string): string {
  return `${current}${delta}`;
}

export function appendReferenceText(
  answer: string,
  referenceNumbers: number[],
): string {
  if (!referenceNumbers.length) {
    return answer;
  }
  return `${answer}\n\n引用：${referenceNumbers
    .map((number) => `[${number}]`)
    .join("")}`;
}

export function resolveChatTerminalState(
  result: ChatRunResponse,
): ChatTerminalState {
  if (result.status === "interrupted") {
    return { status: "interrupted", pendingReview: result };
  }
  return { status: "completed", pendingReview: null };
}
