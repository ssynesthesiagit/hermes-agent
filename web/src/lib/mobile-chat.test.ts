import { describe, expect, it } from "vitest";

import {
  createEmptyMobileActivityState,
  mergeActivityEvent,
  mergeStreamEvent,
  normalizeHistoryShapes,
  normalizeResumeStream,
  normalizeSessionList,
  safeToolDisplayLabel,
  scopeMobileEvent,
  toMobileFileUploadRequest,
  type MobileActivityState,
  type MobileStreamState,
} from "./mobile-chat";

describe("normalizeHistoryShapes", () => {
  it("normalizes gateway rows, rich text blocks, and hides tool scaffolding", () => {
    const messages = normalizeHistoryShapes({
      messages: [
        { role: "user", content: "hello", row_id: 7 },
        { role: "tool", content: "not a transcript bubble" },
        {
          role: "assistant",
          content: [{ type: "text", text: "first" }, { text: "second" }],
        },
        { role: "assistant", text: "hidden", display_kind: "hidden" },
      ],
    });

    expect(messages).toEqual([
      { id: "7", role: "user", text: "hello" },
      { id: "history-2", role: "assistant", text: "first\nsecond" },
    ]);
  });
});

describe("normalizeResumeStream", () => {
  it("folds a live inflight turn onto history without losing its prompt", () => {
    const response = {
      messages: [
        { role: "user", content: "earlier" },
        { role: "assistant", content: "earlier answer" },
      ],
      running: true,
      status: "streaming",
      inflight: {
        user: "current prompt",
        assistant: "partial answer",
        streaming: true,
      },
    };

    const state = normalizeResumeStream(response);

    expect(state.streaming).toBe(true);
    expect(state.messages).toEqual([
      { id: "history-0", role: "user", text: "earlier" },
      { id: "history-1", role: "assistant", text: "earlier answer" },
      { id: "mobile-inflight-user", role: "user", text: "current prompt" },
      {
        id: "mobile-inflight-assistant",
        role: "assistant",
        text: "partial answer",
        streaming: true,
      },
    ]);
  });

  it("does not duplicate an inflight prompt when history already contains it", () => {
    const history = {
      messages: [
        { role: "user", content: "current prompt" },
        { role: "assistant", content: "partial answer" },
      ],
    };
    const resume = {
      running: true,
      inflight: {
        user: "current prompt",
        assistant: "partial answer",
        streaming: true,
      },
    };

    const state = normalizeResumeStream(history, resume);

    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({ role: "user", text: "current prompt" });
    expect(state.messages[1]).toMatchObject({
      role: "assistant",
      text: "partial answer",
      streaming: true,
    });
  });

  it("keeps retained failed inflight text visible but settles the stream", () => {
    const state = normalizeResumeStream({
      messages: [],
      running: false,
      inflight: {
        user: "try again",
        assistant: "partial",
        error: "provider unavailable",
        status: "error",
        streaming: false,
      },
    });

    expect(state.streaming).toBe(false);
    expect(state.error).toBe("provider unavailable");
    expect(state.messages).toMatchObject([
      { role: "user", text: "try again" },
      { role: "assistant", text: "partial", error: true },
    ]);
  });
});

describe("mergeStreamEvent", () => {
  const initial: MobileStreamState = {
    messages: [{ id: "user-1", role: "user", text: "hi" }],
    streaming: false,
    error: null,
  };

  it("ignores events from another runtime session", () => {
    const event = {
      type: "message.delta",
      session_id: "old-runtime",
      payload: { text: "stale" },
    };

    expect(scopeMobileEvent(event, "current-runtime")).toBeNull();
    expect(mergeStreamEvent(initial, event, "current-runtime")).toBe(initial);
  });

  it("appends deltas and lets complete provide the authoritative final text", () => {
    const delta = mergeStreamEvent(
      initial,
      {
        type: "message.delta",
        session_id: "current-runtime",
        payload: { text: "partial" },
      },
      "current-runtime",
    );
    const complete = mergeStreamEvent(
      delta,
      {
        type: "message.complete",
        session_id: "current-runtime",
        payload: { text: "final answer", status: "ok" },
      },
      "current-runtime",
    );

    expect(delta.messages.at(-1)).toMatchObject({
      role: "assistant",
      text: "partial",
      streaming: true,
    });
    expect(complete.messages.at(-1)).toMatchObject({
      role: "assistant",
      text: "final answer",
      streaming: false,
    });
    expect(complete.streaming).toBe(false);
  });

  it("uses a fresh assistant id for every streamed turn", () => {
    const firstTurn = mergeStreamEvent(
      initial,
      {
        type: "message.delta",
        session_id: "current-runtime",
        payload: { text: "first" },
      },
      "current-runtime",
    );
    const firstId = firstTurn.messages.at(-1)?.id;
    const settled = mergeStreamEvent(
      firstTurn,
      {
        type: "message.complete",
        session_id: "current-runtime",
        payload: { text: "first" },
      },
      "current-runtime",
    );
    const secondTurn = mergeStreamEvent(
      settled,
      {
        type: "message.delta",
        session_id: "current-runtime",
        payload: { text: "second" },
      },
      "current-runtime",
    );

    expect(secondTurn.messages.at(-1)?.id).not.toBe(firstId);
    expect(new Set(secondTurn.messages.map((message) => message.id)).size).toBe(
      secondTurn.messages.length,
    );
  });

  it("keeps message.interim visible without duplicating the final response", () => {
    const interim = mergeStreamEvent(
      initial,
      {
        type: "message.interim",
        session_id: "current-runtime",
        payload: { rendered: "working on it" },
      },
      "current-runtime",
    );
    const revisedInterim = mergeStreamEvent(
      interim,
      {
        type: "message.interim",
        session_id: "current-runtime",
        payload: { text: "still working" },
      },
      "current-runtime",
    );
    const complete = mergeStreamEvent(
      revisedInterim,
      {
        type: "message.complete",
        session_id: "current-runtime",
        payload: { text: "final response" },
      },
      "current-runtime",
    );

    expect(interim.messages.at(-1)).toMatchObject({
      role: "assistant",
      text: "working on it",
    });
    expect(revisedInterim.messages).toHaveLength(initial.messages.length + 1);
    expect(revisedInterim.messages.at(-1)?.text).toBe("still working");
    expect(complete.messages).toEqual([
      ...initial.messages,
      expect.objectContaining({
        role: "assistant",
        text: "final response",
      }),
    ]);
  });

  it("surfaces scoped gateway errors without accepting stale errors", () => {
    const next = mergeStreamEvent(
      initial,
      {
        type: "error",
        session_id: "current-runtime",
        payload: { message: "provider unavailable" },
      },
      "current-runtime",
    );

    expect(next.error).toBe("provider unavailable");
    expect(next.streaming).toBe(false);
  });
});

describe("mergeActivityEvent", () => {
  const initialActivity: MobileActivityState = createEmptyMobileActivityState();
  const scopedEvent = (
    type: string,
    payload: Record<string, unknown>,
    session_id = "current-runtime",
  ) => ({ type, payload, session_id });

  it("ignores activity from another runtime session", () => {
    const stale = mergeActivityEvent(
      initialActivity,
      scopedEvent("status.update", { message: "stale status" }, "old-runtime"),
      "current-runtime",
    );

    expect(stale).toBe(initialActivity);
  });

  it("accumulates visible reasoning and lets reasoning.available finalize it", () => {
    const partial = mergeActivityEvent(
      mergeActivityEvent(
        initialActivity,
        scopedEvent("thinking.delta", { text: "Plan " }),
        "current-runtime",
      ),
        scopedEvent("reasoning.delta", { rendered: "the response" }),
      "current-runtime",
    );
    const finalized = mergeActivityEvent(
      partial,
      scopedEvent("reasoning.available", {
        text: "Visible gateway reasoning",
        hidden_reasoning: "must not be projected",
      }),
      "current-runtime",
    );

    expect(partial.items).toMatchObject([
      { kind: "reasoning", label: "MODEL", text: "Plan the response", state: "running" },
    ]);
    expect(finalized.items).toEqual([
      expect.objectContaining({
        kind: "reasoning",
        text: "Visible gateway reasoning",
        state: "complete",
      }),
    ]);
    expect(JSON.stringify(finalized)).not.toContain("must not be projected");
  });

  it("keeps one sanitized tool item through generic lifecycle states", () => {
    const secretToolId = "SECRET_MOBILE_TOOL_ID_9f3a7c1e_should_not_survive";
    const recognizableSecret = "SECRET_MOBILE_TOOL_ID";
    const secrets = {
      context: "SECRET_CONTEXT",
      preview: "SECRET_PREVIEW",
      summary: "SECRET_SUMMARY",
      args_text: "SECRET_ARGS_TEXT",
      result_text: "SECRET_RESULT_TEXT",
      command: "SECRET_COMMAND",
      error: "SECRET_ERROR",
      unknown: "SECRET_UNKNOWN",
    };
    const started = mergeActivityEvent(
      initialActivity,
      scopedEvent("tool.start", {
        tool_id: secretToolId,
        name: "terminal",
        ...secrets,
        duration_s: 1.25,
      }),
      "current-runtime",
    );
    const progressed = mergeActivityEvent(
      started,
      scopedEvent("tool.progress", {
        tool_id: secretToolId,
        name: "terminal",
        ...secrets,
        duration_s: 2.5,
      }),
      "current-runtime",
    );
    const completed = mergeActivityEvent(
      progressed,
      scopedEvent("tool.complete", {
        tool_id: secretToolId,
        name: "terminal",
        ...secrets,
        error: false,
        duration_s: 3.75,
        tokens: 999,
      }),
      "current-runtime",
    );
    const errored = mergeActivityEvent(
      completed,
      scopedEvent("tool.complete", {
        tool_id: secretToolId,
        name: "terminal",
        ...secrets,
        duration_s: 100_000,
      }),
      "current-runtime",
    );

    expect(started.items).toHaveLength(1);
    expect(progressed.items).toHaveLength(1);
    expect(started.items[0]).toMatchObject({
      label: "terminal",
      text: "Tool call started",
      state: "running",
      durationSeconds: 1.25,
    });
    expect(progressed.items[0]).toMatchObject({
      label: "terminal",
      text: "Tool is running",
      state: "running",
      durationSeconds: 2.5,
    });
    expect(completed.items).toEqual([
      expect.objectContaining({
        label: "terminal",
        text: "Tool complete",
        state: "complete",
        durationSeconds: 3.75,
      }),
    ]);
    expect(errored.items).toEqual([
      expect.objectContaining({
        label: "terminal",
        text: "Tool reported an error",
        state: "error",
        durationSeconds: 86_400,
      }),
    ]);
    const lifecycleStates = [started, progressed, completed];
    expect(lifecycleStates.every((state) => state.items.length === 1)).toBe(true);
    expect(new Set(lifecycleStates.map((state) => state.items[0].id)).size).toBe(1);
    for (const state of [started, progressed, completed, errored]) {
      for (const item of state.items) {
        expect(item).not.toHaveProperty("toolId");
      }
    }
    const projected = JSON.stringify([started, progressed, completed, errored]);
    expect(projected).not.toContain(secretToolId);
    expect(projected).not.toContain(recognizableSecret);
    for (const secret of Object.values(secrets)) {
      expect(projected).not.toContain(secret);
    }
    expect(projected).not.toContain("999");
  });

  it("projects safe status, generating, background, and tool errors", () => {
    const status = mergeActivityEvent(
      initialActivity,
      scopedEvent("status.update", {
        status: "working",
        message: "Waiting for the next safe step",
        arbitrary: "ignore me",
      }),
      "current-runtime",
    );
    const generating = mergeActivityEvent(
      status,
      scopedEvent("tool.generating", { name: "web_search", args_text: "hidden" }),
      "current-runtime",
    );
    const background = mergeActivityEvent(
      generating,
      scopedEvent("background.complete", {
        name: "indexer",
        summary: "Index refreshed",
        secret: "ignore me",
      }),
      "current-runtime",
    );
    const errored = mergeActivityEvent(
      background,
      scopedEvent("tool.complete", {
        tool_id: "tool-error",
        name: "terminal",
        error: "private stack details",
        result_text: "private output",
      }),
      "current-runtime",
    );

    expect(errored.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "status", text: "working: Waiting for the next safe step" }),
        expect.objectContaining({ kind: "tool", label: "web_search", state: "running" }),
        expect.objectContaining({ kind: "background", text: "Index refreshed", state: "complete" }),
        expect.objectContaining({ kind: "tool", label: "terminal", state: "error", text: "Tool reported an error" }),
      ]),
    );
    const projected = JSON.stringify(errored);
    expect(projected).not.toContain("ignore me");
    expect(projected).not.toContain("hidden");
    expect(projected).not.toContain("private stack details");
    expect(projected).not.toContain("private output");
  });

  it("uses a generic label for hostile tool names across every tool lifecycle event", () => {
    const hostileNames = [
      "terminal; cat ~/.hermes/.env",
      "terminal\nSECRET_TOKEN",
      `terminal-${"x".repeat(200)}`,
      "SECRET_TOKEN=do-not-render",
    ];
    let state = initialActivity;

    hostileNames.forEach((name, index) => {
      const toolId = `hostile-${index}`;
      state = mergeActivityEvent(
        state,
        scopedEvent("tool.generating", { name, tool_id: `${toolId}-generating` }),
        "current-runtime",
      );
      expect(state.items.at(-1)).toMatchObject({ kind: "tool", label: "tool" });
      expect(JSON.stringify(state)).not.toContain(name);

      state = mergeActivityEvent(
        state,
        scopedEvent("tool.start", { name, tool_id: toolId }),
        "current-runtime",
      );
      expect(state.items.at(-1)).toMatchObject({ kind: "tool", label: "tool" });
      expect(JSON.stringify(state)).not.toContain(name);

      state = mergeActivityEvent(
        state,
        scopedEvent("tool.progress", { name, tool_id: toolId }),
        "current-runtime",
      );
      expect(state.items.at(-1)).toMatchObject({ kind: "tool", label: "tool" });
      expect(JSON.stringify(state)).not.toContain(name);

      state = mergeActivityEvent(
        state,
        scopedEvent("tool.complete", { name, tool_id: toolId, error: false }),
        "current-runtime",
      );
      expect(state.items.at(-1)).toMatchObject({ kind: "tool", label: "tool" });
      expect(JSON.stringify(state)).not.toContain(name);
    });

    expect(JSON.stringify(state)).not.toContain("SECRET_TOKEN");
    expect(
      state.items
        .filter((item) => item.kind === "tool")
        .every((item) => item.label === "tool"),
    ).toBe(true);
  });

  it("preserves the familiar label for an established Hermes tool", () => {
    expect(safeToolDisplayLabel("terminal")).toBe("terminal");
    expect(
      mergeActivityEvent(
        initialActivity,
        scopedEvent("tool.generating", { name: "terminal" }),
        "current-runtime",
      ).items,
    ).toEqual([
      expect.objectContaining({ kind: "tool", label: "terminal" }),
    ]);
  });

  it("bounds activity items and detail text", () => {
    let state = initialActivity;
    for (let index = 0; index < 40; index += 1) {
      state = mergeActivityEvent(
        state,
        scopedEvent("background.complete", {
          name: `job-${index}`,
          message: `event-${index}-${"x".repeat(500)}`,
        }),
        "current-runtime",
      );
    }

    expect(state.items).toHaveLength(30);
    expect(state.items[0].label).toBe("job-10");
    expect(state.items.at(-1)?.label).toBe("job-39");
    expect(state.items.every((item) => item.text.length <= 360)).toBe(true);
  });
});

describe("mobile payload helpers", () => {
  it("converts image data URLs to image.attach_bytes params", () => {
    expect(
      toMobileFileUploadRequest({
        sessionId: "runtime-1",
        file: { name: "photo.JPG", type: "image/jpeg" },
        dataUrl: "data:image/jpeg;base64, YWJj\nZA==",
      }),
    ).toEqual({
      method: "image.attach_bytes",
      params: {
        session_id: "runtime-1",
        content_base64: "YWJjZA==",
        filename: "photo.JPG",
        ext: ".jpg",
      },
    });
  });

  it("keeps non-image data URLs for file.attach", () => {
    expect(
      toMobileFileUploadRequest({
        sessionId: "runtime-2",
        file: { name: "notes.txt", type: "text/plain" },
        dataUrl: "data:text/plain;base64,SGVsbG8=",
      }),
    ).toEqual({
      method: "file.attach",
      params: {
        session_id: "runtime-2",
        data_url: "data:text/plain;base64,SGVsbG8=",
        name: "notes.txt",
      },
    });
  });

  it("routes SVG and unsupported image MIME types through file.attach", () => {
    expect(
      toMobileFileUploadRequest({
        sessionId: "runtime-3",
        file: { name: "diagram.svg", type: "image/svg+xml" },
        dataUrl: "data:image/svg+xml;base64,PHN2Zz4=",
      }),
    ).toEqual({
      method: "file.attach",
      params: {
        session_id: "runtime-3",
        data_url: "data:image/svg+xml;base64,PHN2Zz4=",
        name: "diagram.svg",
      },
    });

    expect(
      toMobileFileUploadRequest({
        sessionId: "runtime-4",
        file: { name: "photo.heic", type: "image/heic" },
        dataUrl: "data:image/heic;base64,AA==",
      }).method,
    ).toBe("file.attach");
  });
});

describe("normalizeSessionList", () => {
  it("keeps the compact session fields used by the mobile picker", () => {
    expect(
      normalizeSessionList({
        sessions: [
          {
            id: "durable-1",
            title: "Planning",
            preview: "Next steps",
            started_at: "12",
            message_count: 4,
            source: "web",
          },
          { title: "missing id" },
        ],
      }),
    ).toEqual([
      {
        id: "durable-1",
        title: "Planning",
        preview: "Next steps",
        started_at: 12,
        message_count: 4,
        source: "web",
      },
    ]);
  });
});
