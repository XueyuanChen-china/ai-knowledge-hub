import type {
  ChatRunResponse,
  ChatStreamErrorEvent,
  ChatStreamEventName,
  ChatStreamNodeEvent,
  ChatStreamStartEvent,
} from "@/lib/api/types";

export type ChatStreamEventData =
  | ChatStreamStartEvent
  | ChatStreamNodeEvent
  | string
  | number[]
  | ChatRunResponse
  | ChatStreamErrorEvent;

/**
 * 解析一个已经按 SSE 空行分隔的事件块。
 * answer 是纯文本，其他事件仍按 JSON 解析；data 行只移除协议要求的一个前导空格，
 * 避免把模型回答中的缩进和换行误删。
 */
export function parseSseEventChunk(chunk: string): {
  event: ChatStreamEventName;
  data: ChatStreamEventData;
} | null {
  const lines = chunk.split(/\r?\n/);
  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      const value = line.slice("data:".length);
      dataLines.push(value.startsWith(" ") ? value.slice(1) : value);
    }
  }

  if (!dataLines.length) {
    return null;
  }

  const rawData = dataLines.join("\n");
  try {
    if (eventName === "answer") {
      return {
        event: eventName as ChatStreamEventName,
        data: rawData,
      };
    }

    if (eventName === "references") {
      return {
        event: eventName as ChatStreamEventName,
        data: JSON.parse(rawData) as number[],
      };
    }

    return {
      event: eventName as ChatStreamEventName,
      data: JSON.parse(rawData) as ChatStreamEventData,
    };
  } catch {
    return null;
  }
}
