import { describe, expect, it } from "vitest";

import { parseSseEventChunk } from "@/lib/api/sse";

describe("parseSseEventChunk", () => {
  it("keeps answer text and multiline whitespace", () => {
    expect(
      parseSseEventChunk("event: answer\ndata: 第一段\ndata: \ndata: 第二段"),
    ).toEqual({
      event: "answer",
      data: "第一段\n\n第二段",
    });
  });

  it("parses structured node events as JSON", () => {
    expect(
      parseSseEventChunk(
        'event: node\ndata: {"node":"retrieve","retrieval_hit_count":3}',
      ),
    ).toEqual({
      event: "node",
      data: { node: "retrieve", retrieval_hit_count: 3 },
    });
  });

  it("parses references separately from answer text", () => {
    expect(parseSseEventChunk("event: references\ndata: [1,2]")).toEqual({
      event: "references",
      data: [1, 2],
    });
  });

  it("ignores malformed JSON and empty protocol blocks", () => {
    expect(parseSseEventChunk("event: node\ndata: {bad json}")).toBeNull();
    expect(parseSseEventChunk(": heartbeat\n")).toBeNull();
  });
});
