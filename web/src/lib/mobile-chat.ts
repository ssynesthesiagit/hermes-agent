export type MobileMessageRole = "user" | "assistant" | "system";

export interface MobileTranscriptMessage {
  id: string;
  role: MobileMessageRole;
  text: string;
  timestamp?: number;
  streaming?: boolean;
  optimistic?: boolean;
  error?: boolean;
}

export interface MobileSessionSummary {
  id: string;
  title: string;
  preview: string;
  started_at: number;
  message_count: number;
  source: string;
}

export interface MobileGatewayEvent {
  type: string;
  session_id?: string;
  payload?: unknown;
}

export interface MobileStreamState {
  messages: MobileTranscriptMessage[];
  streaming: boolean;
  error: string | null;
}

export type MobileActivityKind = "reasoning" | "tool" | "status" | "background";
export type MobileActivityItemState = "running" | "complete" | "error";

export interface MobileActivityItem {
  id: string;
  kind: MobileActivityKind;
  label: string;
  text: string;
  state: MobileActivityItemState;
  durationSeconds?: number;
}

export interface MobileActivityState {
  items: MobileActivityItem[];
}

export function createEmptyMobileActivityState(): MobileActivityState {
  return { items: [] };
}

export interface MobileFileDescriptor {
  name: string;
  type?: string | null;
}

export type MobileFileUploadRequest =
  | {
      method: "image.attach_bytes";
      params: {
        session_id: string;
        content_base64: string;
        filename: string;
        ext: string;
      };
    }
  | {
      method: "file.attach";
      params: {
        session_id: string;
        data_url: string;
        name: string;
      };
    };

const STREAMING_MESSAGE_PREFIX = "mobile-streaming-assistant";
const INTERIM_MESSAGE_PREFIX = "mobile-interim-assistant";
const INFLIGHT_USER_PREFIX = "mobile-inflight-user";
const INFLIGHT_ASSISTANT_PREFIX = "mobile-inflight-assistant";
const INFLIGHT_CORRECTION_PREFIX = "mobile-inflight-correction";
const MOBILE_ACTIVITY_ITEM_LIMIT = 30;
const MOBILE_ACTIVITY_TEXT_LIMIT = 360;
const TOOL_ID_HASH_INPUT_LIMIT = 128;
const TOOL_ID_HASH_LENGTH = 8;
const REASONING_ACTIVITY_ID = "mobile-activity-reasoning";
const STATUS_ACTIVITY_ID = "mobile-activity-status";
// Keep this finite and explicit. These are established Hermes core tool
// identifiers from toolsets.py; dynamic MCP/plugin names are intentionally
// not displayable here.
const SAFE_TOOL_DISPLAY_LABELS = new Set([
  "web_search",
  "web_extract",
  "terminal",
  "process",
  "read_file",
  "write_file",
  "patch",
  "search_files",
  "vision_analyze",
  "image_generate",
  "skills_list",
  "skill_view",
  "skill_manage",
  "browser_navigate",
  "browser_snapshot",
  "browser_click",
  "browser_type",
  "browser_scroll",
  "browser_back",
  "browser_press",
  "browser_get_images",
  "browser_vision",
  "browser_console",
  "browser_cdp",
  "browser_dialog",
  "browser_exec",
  "text_to_speech",
  "todo",
  "memory",
  "session_search",
  "clarify",
  "execute_code",
  "delegate_task",
  "cronjob",
  "ha_list_entities",
  "ha_get_state",
  "ha_list_services",
  "ha_call_service",
  "kanban_show",
  "kanban_list",
  "kanban_complete",
  "kanban_block",
  "kanban_request_review",
  "kanban_request_changes",
  "kanban_heartbeat",
  "kanban_comment",
  "kanban_create",
  "kanban_link",
  "kanban_unblock",
  "kanban_attach",
  "kanban_attach_url",
  "kanban_attachments",
  "computer_use",
]);
const SUPPORTED_IMAGE_MIMES = new Set([
  "image/bmp",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
]);
const SUPPORTED_IMAGE_EXTENSIONS = /\.(bmp|gif|jpe?g|png|webp)$/i;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function textFromContent(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map(textFromContent).filter(Boolean).join("\n");
  }

  const record = asRecord(value);
  if (!record) return "";
  if (typeof record.text === "string") return record.text;
  if (typeof record.rendered === "string") return record.rendered;
  if (typeof record.content !== "undefined") {
    return textFromContent(record.content);
  }
  if (typeof record.value !== "undefined") return textFromContent(record.value);
  return "";
}

function historyRows(input: unknown): unknown[] {
  if (Array.isArray(input)) return input;
  const record = asRecord(input);
  if (!record) return [];
  if (Array.isArray(record.messages)) return record.messages;
  if (Array.isArray(record.history)) return record.history;
  if (asRecord(record.result)) return historyRows(record.result);
  if ("role" in record || "content" in record || "text" in record) {
    return [record];
  }
  return [];
}

function normalizeRole(value: unknown): MobileMessageRole | null {
  if (typeof value !== "string") return null;
  switch (value.toLowerCase()) {
    case "user":
    case "human":
      return "user";
    case "assistant":
    case "ai":
    case "bot":
      return "assistant";
    case "system":
      return "system";
    default:
      return null;
  }
}

/** Convert the gateway's history/message projections into visible mobile rows. */
export function normalizeHistoryShapes(input: unknown): MobileTranscriptMessage[] {
  return historyRows(input).flatMap((item, index) => {
    const row = asRecord(item);
    if (!row || row.display_kind === "hidden" || row.display_kind === "compaction") {
      return [];
    }

    const role = normalizeRole(row.role ?? row.type);
    if (!role) return [];
    const text = textFromContent(row.text ?? row.content ?? row.rendered);
    if (!text && role !== "system") return [];

    const explicitId = row.id ?? row.row_id ?? row.message_id;
    const id =
      typeof explicitId === "string" || typeof explicitId === "number"
        ? String(explicitId)
        : `history-${index}`;
    const timestamp =
      typeof row.timestamp === "number"
        ? row.timestamp
        : typeof row.timestamp === "string" && Number.isFinite(Number(row.timestamp))
          ? Number(row.timestamp)
          : undefined;

    return [
      {
        id,
        role,
        text,
        ...(timestamp !== undefined ? { timestamp } : {}),
      },
    ];
  });
}

function resumePayload(input: unknown): Record<string, unknown> {
  const direct = asRecord(input);
  if (!direct) return {};
  const nested = asRecord(direct.result);
  return nested &&
      ("inflight" in nested || "running" in nested || "messages" in nested || "session_id" in nested)
    ? nested
    : direct;
}

function nonEmptyText(value: unknown): string | null {
  const text = textFromContent(value);
  return text.trim() ? text : null;
}

function sameVisibleText(left: string, right: string): boolean {
  return left.trim() === right.trim();
}

function generatedId(prefix: string, messages: MobileTranscriptMessage[], suffix = ""): string {
  const usedIds = new Set(messages.map((message) => message.id));
  const base = `${prefix}${suffix ? `-${suffix}` : ""}`;
  if (!usedIds.has(base)) return base;

  let sequence = 2;
  while (usedIds.has(`${base}-${sequence}`)) sequence += 1;
  return `${base}-${sequence}`;
}

function terminalInflightStatus(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return new Set([
    "cancelled",
    "canceled",
    "complete",
    "completed",
    "done",
    "error",
    "idle",
    "interrupted",
  ]).has(value.toLowerCase());
}

function resumeIsStreaming(
  payload: Record<string, unknown>,
  inflight: Record<string, unknown> | null,
): boolean {
  const status = typeof payload.status === "string" ? payload.status : "";
  const inflightStatus = typeof inflight?.status === "string" ? inflight.status : "";
  if (
    terminalInflightStatus(status) ||
    terminalInflightStatus(inflightStatus) ||
    Boolean(inflight?.error)
  ) {
    return false;
  }

  return Boolean(
    payload.running === true ||
      inflight?.streaming === true ||
      ["active", "resuming", "running", "streaming"].includes(status.toLowerCase()) ||
      ["active", "resuming", "running", "streaming"].includes(inflightStatus.toLowerCase()),
  );
}

function mergeInflightProjection(
  history: MobileTranscriptMessage[],
  inflight: Record<string, unknown>,
  streaming: boolean,
  error: string | null,
): MobileTranscriptMessage[] {
  const messages = [...history];
  const user = nonEmptyText(inflight.user);
  const assistant = textFromContent(inflight.assistant);

  let userIndex = -1;
  if (user) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (
        message.role !== "user" ||
        (!message.id.startsWith(INFLIGHT_USER_PREFIX) && !sameVisibleText(message.text, user))
      ) {
        continue;
      }

      const after = messages.slice(index + 1);
      const hasLiveAssistant = after.some(
        (message) =>
          message.role === "assistant" &&
          (message.streaming || message.id.startsWith(INFLIGHT_ASSISTANT_PREFIX)),
      );
      const hasMatchingAssistant = assistant
        ? after.some(
            (message) =>
              message.role === "assistant" && sameVisibleText(message.text, assistant),
          )
        : false;
      const hasCommittedAssistant = after.some((message) => message.role === "assistant");

      if (!hasCommittedAssistant || hasLiveAssistant || hasMatchingAssistant) {
        userIndex = index;
        break;
      }
    }

    if (userIndex < 0) {
      userIndex = messages.length;
      messages.push({
        id: generatedId(INFLIGHT_USER_PREFIX, messages),
        role: "user",
        text: user,
      });
    }
  }

  const assistantShouldShow = Boolean(assistant) || streaming;
  if (assistantShouldShow) {
    const startIndex = userIndex >= 0 ? userIndex + 1 : 0;
    let assistantIndex = -1;
    for (let index = messages.length - 1; index >= startIndex; index -= 1) {
      const message = messages[index];
      if (
        message.role === "assistant" &&
        (message.id.startsWith(INFLIGHT_ASSISTANT_PREFIX) ||
          message.streaming ||
          (assistant && sameVisibleText(message.text, assistant)))
      ) {
        assistantIndex = index;
        break;
      }
    }

    const nextAssistant = {
      id:
        assistantIndex >= 0
          ? messages[assistantIndex].id
          : generatedId(INFLIGHT_ASSISTANT_PREFIX, messages),
      role: "assistant" as const,
      text:
        assistantIndex >= 0 && !assistant
          ? messages[assistantIndex].text
          : assistant,
      ...(streaming ? { streaming: true } : {}),
      ...(error ? { error: true } : {}),
    };

    if (assistantIndex >= 0) messages[assistantIndex] = nextAssistant;
    else messages.push(nextAssistant);
  }

  const corrections = Array.isArray(inflight.corrections)
    ? inflight.corrections.map(nonEmptyText).filter((value): value is string => Boolean(value))
    : [];
  corrections.forEach((correction, index) => {
    const idPrefix = `${INFLIGHT_CORRECTION_PREFIX}-${index + 1}`;
    const alreadyPresent = messages.some(
      (message) =>
        message.role === "user" &&
        (message.id.startsWith(idPrefix) || sameVisibleText(message.text, correction)),
    );
    if (!alreadyPresent) {
      messages.push({
        id: generatedId(INFLIGHT_CORRECTION_PREFIX, messages, String(index + 1)),
        role: "user",
        text: correction,
      });
    }
  });

  return messages;
}

/** Rebuild a mobile transcript from durable history plus a live resume projection. */
export function normalizeResumeStream(
  historyInput: unknown,
  resumeInput: unknown = historyInput,
): MobileStreamState {
  const history = normalizeHistoryShapes(historyInput);
  const historyPayload = resumePayload(historyInput);
  const payload = resumePayload(resumeInput);
  const inflightValue = "inflight" in payload ? payload.inflight : historyPayload.inflight;
  const inflight = asRecord(inflightValue);
  const streaming = resumeIsStreaming(payload, inflight);
  const error =
    nonEmptyText(inflight?.error) ??
    nonEmptyText(payload.error) ??
    nonEmptyText(historyPayload.error);

  return {
    messages: inflight
      ? mergeInflightProjection(history, inflight, streaming, error)
      : history,
    streaming,
    error,
  };
}

/** Normalize the compact session.list response without trusting optional fields. */
export function normalizeSessionList(input: unknown): MobileSessionSummary[] {
  const record = asRecord(input);
  const rows = record && Array.isArray(record.sessions) ? record.sessions : [];

  return rows.flatMap((item) => {
    const row = asRecord(item);
    if (!row || (typeof row.id !== "string" && typeof row.id !== "number")) {
      return [];
    }
    const numeric = (value: unknown, fallback = 0) => {
      const parsed = typeof value === "number" ? value : Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    };
    return [
      {
        id: String(row.id),
        title: typeof row.title === "string" ? row.title : "",
        preview: typeof row.preview === "string" ? row.preview : "",
        started_at: numeric(row.started_at),
        message_count: numeric(row.message_count),
        source: typeof row.source === "string" ? row.source : "",
      },
    ];
  });
}

/** Reject events from old/replaced runtime sessions before they touch the UI. */
export function scopeMobileEvent(
  event: MobileGatewayEvent,
  runtimeSessionId: string | null,
): MobileGatewayEvent | null {
  if (!runtimeSessionId || event.session_id !== runtimeSessionId) return null;
  return event;
}

function eventPayload(event: MobileGatewayEvent): Record<string, unknown> {
  return asRecord(event.payload) ?? {};
}

function eventText(event: MobileGatewayEvent): string | null {
  const payload = eventPayload(event);
  const text = textFromContent(payload.text ?? payload.rendered);
  return text || null;
}

function eventError(event: MobileGatewayEvent): string | null {
  const payload = eventPayload(event);
  const explicit = payload.error ?? payload.failure_reason ?? payload.message;
  if (typeof explicit === "string" && explicit.trim()) return explicit.trim();
  if (payload.status === "error") return eventText(event) ?? "Hermes could not complete that turn.";
  return null;
}

function clipActivityText(value: string): string {
  const text = value;
  return text.length <= MOBILE_ACTIVITY_TEXT_LIMIT
    ? text
    : `${text.slice(0, MOBILE_ACTIVITY_TEXT_LIMIT - 1)}…`;
}

function safeActivityField(
  payload: Record<string, unknown>,
  field: string,
): string | null {
  const value = payload[field];
  if (typeof value !== "string" || !value.trim()) return null;
  return clipActivityText(value);
}

function firstSafeActivityField(
  payload: Record<string, unknown>,
  fields: readonly string[],
): string | null {
  for (const field of fields) {
    const value = safeActivityField(payload, field);
    if (value) return value;
  }
  return null;
}

function firstActivityDelta(
  payload: Record<string, unknown>,
  fields: readonly string[],
): string | null {
  for (const field of fields) {
    const value = payload[field];
    if (typeof value !== "string" || !value.trim()) continue;
    return value.length <= MOBILE_ACTIVITY_TEXT_LIMIT
      ? value
      : `${value.slice(0, MOBILE_ACTIVITY_TEXT_LIMIT - 1)}…`;
  }
  return null;
}

function safeActivityKey(value: unknown): string | undefined {
  const raw =
    typeof value === "string"
      ? value.trim()
      : typeof value === "number" && Number.isFinite(value)
        ? String(value)
        : "";
  const key = raw.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").slice(0, 80);
  return key || undefined;
}

function opaqueToolActivityKey(value: unknown): string | undefined {
  const raw =
    typeof value === "string"
      ? value.trim()
      : typeof value === "number" && Number.isFinite(value)
        ? String(value)
        : "";
  if (!raw) return undefined;

  const input = raw.slice(0, TOOL_ID_HASH_INPUT_LIMIT);
  let hash = 2_166_136_261;
  for (let index = 0; index < input.length; index += 1) {
    hash = Math.imul(hash ^ input.charCodeAt(index), 16_777_619);
  }

  return (hash >>> 0).toString(16).padStart(TOOL_ID_HASH_LENGTH, "0");
}

function safeDurationSeconds(value: unknown): number | undefined {
  const duration =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim()
        ? Number(value)
        : Number.NaN;
  if (!Number.isFinite(duration) || duration < 0) return undefined;
  return Math.min(duration, 86_400);
}

function toolDurationFields(
  payload: Record<string, unknown>,
): Pick<MobileActivityItem, "durationSeconds"> {
  const durationSeconds = safeDurationSeconds(payload.duration_s);
  return durationSeconds === undefined ? {} : { durationSeconds };
}

/** Return a visible label only for known Hermes built-in tools. */
export function safeToolDisplayLabel(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length > 64 ||
    value.trim() !== value ||
    !/^[a-z0-9_]+$/.test(value) ||
    !SAFE_TOOL_DISPLAY_LABELS.has(value)
  ) {
    return "tool";
  }
  return value;
}

function upsertActivityItem(
  state: MobileActivityState,
  item: MobileActivityItem,
): MobileActivityState {
  const index = state.items.findIndex((current) => current.id === item.id);
  const items = [...state.items];
  if (index >= 0) items[index] = { ...items[index], ...item };
  else items.push(item);

  return {
    items:
      items.length > MOBILE_ACTIVITY_ITEM_LIMIT
        ? items.slice(-MOBILE_ACTIVITY_ITEM_LIMIT)
        : items,
  };
}

function settleActivityItems(
  state: MobileActivityState,
  nextState: MobileActivityItemState,
): MobileActivityState {
  let changed = false;
  const items = state.items.map((item) => {
    if (item.state !== "running") return item;
    changed = true;
    return { ...item, state: nextState };
  });
  return changed ? { items } : state;
}

function toolActivityIdentity(payload: Record<string, unknown>): {
  id: string;
  label: string;
} {
  const label = safeToolDisplayLabel(payload.name);
  const toolKey = opaqueToolActivityKey(payload.tool_id);
  const labelKey = safeActivityKey(label) ?? "unknown";
  return {
    id: toolKey
      ? `mobile-activity-tool-${toolKey}`
      : `mobile-activity-tool-name-${labelKey}`,
    label,
  };
}

function toolGeneratingActivityId(name: string): string {
  return `mobile-activity-tool-generating-${safeActivityKey(name) ?? "unknown"}`;
}

function statusActivityState(status: string | null): MobileActivityItemState {
  switch (status?.toLowerCase()) {
    case "error":
    case "failed":
    case "failure":
      return "error";
    case "complete":
    case "completed":
    case "done":
    case "idle":
    case "ok":
    case "ready":
    case "success":
      return "complete";
    default:
      return "running";
  }
}

function toolHasError(payload: Record<string, unknown>): boolean {
  const error = payload.error;
  return typeof error === "string" ? Boolean(error.trim()) : Boolean(error);
}

/** Merge only allowlisted, current-session gateway activity into a mobile feed. */
export function mergeActivityEvent(
  state: MobileActivityState,
  event: MobileGatewayEvent,
  runtimeSessionId: string | null,
): MobileActivityState {
  if (!scopeMobileEvent(event, runtimeSessionId)) return state;

  const payload = eventPayload(event);

  if (event.type === "reasoning.delta" || event.type === "thinking.delta") {
    const text = firstActivityDelta(payload, ["text", "rendered"]);
    if (!text) return state;
    const existing = state.items.find((item) => item.id === REASONING_ACTIVITY_ID);
    return upsertActivityItem(state, {
      id: REASONING_ACTIVITY_ID,
      kind: "reasoning",
      label: "MODEL",
      text: clipActivityText(`${existing?.text ?? ""}${text}`),
      state: "running",
    });
  }

  if (event.type === "reasoning.available") {
    const text = firstSafeActivityField(payload, ["text", "rendered"]);
    const existing = state.items.find((item) => item.id === REASONING_ACTIVITY_ID);
    if (!text) {
      return existing && existing.state === "running"
        ? upsertActivityItem(state, { ...existing, state: "complete" })
        : state;
    }
    return upsertActivityItem(state, {
      id: REASONING_ACTIVITY_ID,
      kind: "reasoning",
      label: "MODEL",
      text,
      state: "complete",
    });
  }

  if (event.type === "status.update") {
    const status = safeActivityField(payload, "status");
    const detail = firstSafeActivityField(payload, ["message", "text"]);
    const text = detail && status && detail.toLowerCase() !== status.toLowerCase()
      ? clipActivityText(`${status}: ${detail}`)
      : detail ?? status;
    if (!text) return state;
    return upsertActivityItem(state, {
      id: STATUS_ACTIVITY_ID,
      kind: "status",
      label: "GATEWAY",
      text,
      state: statusActivityState(status),
    });
  }

  if (event.type === "tool.generating") {
    const label = safeToolDisplayLabel(payload.name);
    return upsertActivityItem(state, {
      id: toolGeneratingActivityId(label),
      kind: "tool",
      label,
      text: "Preparing tool call",
      state: "running",
      ...toolDurationFields(payload),
    });
  }

  if (
    event.type === "tool.start" ||
    event.type === "tool.progress" ||
    event.type === "tool.complete"
  ) {
    const identity = toolActivityIdentity(payload);
    const existing = state.items.find((item) => item.id === identity.id);
    const label =
      identity.label === "tool" && existing
        ? safeToolDisplayLabel(existing.label)
        : identity.label;

    if (event.type === "tool.start") {
      const generatingId = toolGeneratingActivityId(label);
      const generated = state.items.find((item) => item.id === generatingId);
      const withGenerating = generated
        ? upsertActivityItem(state, {
            ...generated,
            text: "Tool call started",
            state: "complete",
            ...toolDurationFields(payload),
          })
        : state;
      return upsertActivityItem(withGenerating, {
        id: identity.id,
        kind: "tool",
        label,
        text: "Tool call started",
        state: "running",
        ...toolDurationFields(payload),
      });
    }

    if (event.type === "tool.progress") {
      return upsertActivityItem(state, {
        id: identity.id,
        kind: "tool",
        label,
        text: "Tool is running",
        state: "running",
        ...toolDurationFields(payload),
      });
    }

    const error = toolHasError(payload);
    return upsertActivityItem(state, {
      id: identity.id,
      kind: "tool",
      label,
      text: error ? "Tool reported an error" : "Tool complete",
      state: error ? "error" : "complete",
      ...toolDurationFields(payload),
    });
  }

  if (event.type === "background.complete") {
    const name = firstSafeActivityField(payload, ["name"]) ?? "BACKGROUND";
    return upsertActivityItem(state, {
      id: `mobile-activity-background-${safeActivityKey(name) ?? "task"}`,
      kind: "background",
      label: name,
      text:
        firstSafeActivityField(payload, ["message", "summary"]) ??
        "Background task complete",
      state: "complete",
    });
  }

  if (event.type === "message.complete") {
    return settleActivityItems(state, "complete");
  }

  if (event.type === "error") {
    return settleActivityItems(state, "error");
  }

  return state;
}

/** Merge only current-session stream events into the mobile transcript. */
export function mergeStreamEvent(
  state: MobileStreamState,
  event: MobileGatewayEvent,
  runtimeSessionId: string | null,
): MobileStreamState {
  if (!scopeMobileEvent(event, runtimeSessionId)) return state;

  if (event.type === "message.interim") {
    const text = eventText(event);
    const interimMessages = state.messages.filter((message) =>
      message.id.startsWith(INTERIM_MESSAGE_PREFIX),
    );
    const hasStreamingAssistant = state.messages.some(
      (message) => message.role === "assistant" && message.streaming,
    );

    if (!text) return state;

    if (hasStreamingAssistant) {
      if (interimMessages.length === 0) return state;
      return {
        ...state,
        messages: state.messages.filter(
          (message) => !message.id.startsWith(INTERIM_MESSAGE_PREFIX),
        ),
      };
    }

    const existing = interimMessages.at(-1);
    const messages = state.messages.filter(
      (message) => !message.id.startsWith(INTERIM_MESSAGE_PREFIX),
    );
    messages.push({
      id: existing?.id ?? generatedId(INTERIM_MESSAGE_PREFIX, messages),
      role: "assistant",
      text,
    });
    return { ...state, messages };
  }

  if (event.type === "message.delta") {
    const text = eventText(event) ?? "";
    const messages = state.messages.filter(
      (message) => !message.id.startsWith(INTERIM_MESSAGE_PREFIX),
    );
    const existingIndex = messages.findLastIndex(
      (message) => message.role === "assistant" && message.streaming,
    );

    if (existingIndex >= 0) {
      const existing = messages[existingIndex];
      messages[existingIndex] = {
        ...existing,
        text: existing.text + text,
        streaming: true,
        error: false,
      };
    } else {
      const usedIds = new Set(messages.map((message) => message.id));
      let sequence = 1;
      let id = `${STREAMING_MESSAGE_PREFIX}-${sequence}`;
      while (usedIds.has(id)) {
        sequence += 1;
        id = `${STREAMING_MESSAGE_PREFIX}-${sequence}`;
      }
      messages.push({
        id,
        role: "assistant",
        text,
        streaming: true,
      });
    }

    return { messages, streaming: true, error: null };
  }

  if (event.type === "message.complete") {
    const text = eventText(event);
    const error = eventError(event);
    const hasStreamingAssistant = state.messages.some(
      (message) => message.role === "assistant" && message.streaming,
    );
    const messages =
      text || hasStreamingAssistant
        ? state.messages.filter(
            (message) => !message.id.startsWith(INTERIM_MESSAGE_PREFIX),
          )
        : [...state.messages];
    const existingIndex = messages.findLastIndex(
      (message) => message.role === "assistant" && message.streaming,
    );

    if (existingIndex >= 0) {
      const existing = messages[existingIndex];
      messages[existingIndex] = {
        ...existing,
        text: text ?? existing.text,
        streaming: false,
        ...(error ? { error: true } : {}),
      };
    } else if (text) {
      const usedIds = new Set(messages.map((message) => message.id));
      let sequence = 1;
      let id = `${STREAMING_MESSAGE_PREFIX}-${sequence}`;
      while (usedIds.has(id)) {
        sequence += 1;
        id = `${STREAMING_MESSAGE_PREFIX}-${sequence}`;
      }
      messages.push({
        id,
        role: "assistant",
        text,
        ...(error ? { error: true } : {}),
      });
    }

    return { messages, streaming: false, error };
  }

  if (event.type === "error") {
    const payload = eventPayload(event);
    const message =
      typeof payload.message === "string" && payload.message.trim()
        ? payload.message.trim()
        : "The gateway returned an error.";
    return {
      ...state,
      messages: state.messages.map((item) =>
        item.streaming ? { ...item, streaming: false, error: true } : item,
      ),
      streaming: false,
      error: message,
    };
  }

  return state;
}

export function base64FromDataUrl(dataUrl: string): string {
  const comma = dataUrl.indexOf(",");
  return (comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl).replace(/\s/g, "");
}

function extensionForFile(file: MobileFileDescriptor): string {
  const match = file.name.match(/(\.[a-z0-9]{1,10})$/i);
  if (match) return match[1].toLowerCase();

  const mime = (file.type ?? "").split(";", 1)[0].toLowerCase();
  const mimeExtensions: Record<string, string> = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
  };
  return mimeExtensions[mime] ?? "";
}

function isSupportedImageFile(file: MobileFileDescriptor): boolean {
  const mime = (file.type ?? "").split(";", 1)[0].toLowerCase();
  if (mime) return SUPPORTED_IMAGE_MIMES.has(mime);
  return SUPPORTED_IMAGE_EXTENSIONS.test(file.name);
}

/** Build the exact gateway attachment request from a FileReader data URL. */
export function toMobileFileUploadRequest(args: {
  sessionId: string;
  file: MobileFileDescriptor;
  dataUrl: string;
}): MobileFileUploadRequest {
  const { sessionId, file, dataUrl } = args;
  if (isSupportedImageFile(file)) {
    return {
      method: "image.attach_bytes",
      params: {
        session_id: sessionId,
        content_base64: base64FromDataUrl(dataUrl),
        filename: file.name,
        ext: extensionForFile(file),
      },
    };
  }

  return {
    method: "file.attach",
    params: {
      session_id: sessionId,
      data_url: dataUrl,
      name: file.name,
    },
  };
}
