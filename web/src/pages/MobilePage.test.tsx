// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  anyHandlers: new Set<(event: unknown) => void>(),
  stateHandlers: new Set<(state: string) => void>(),
  getActiveProfile: vi.fn(),
  getProfiles: vi.fn(),
  request: vi.fn(),
  state: "idle",
}));

vi.mock("@/lib/api", () => ({
  api: {
    getActiveProfile: mocks.getActiveProfile,
    getProfiles: mocks.getProfiles,
  },
}));

vi.mock("@/lib/gatewayClient", () => ({
  GatewayClient: class {
    get connectionState() {
      return mocks.state;
    }

    async connect() {
      mocks.state = "connecting";
      for (const handler of mocks.stateHandlers) handler("connecting");
      mocks.state = "open";
      for (const handler of mocks.stateHandlers) handler("open");
    }

    close() {
      mocks.state = "closed";
    }

    onAny(handler: (event: unknown) => void) {
      mocks.anyHandlers.add(handler);
      return () => mocks.anyHandlers.delete(handler);
    }

    onState(handler: (state: string) => void) {
      mocks.stateHandlers.add(handler);
      handler(mocks.state);
      return () => mocks.stateHandlers.delete(handler);
    }

    request<T>(method: string, params: Record<string, unknown>) {
      return mocks.request(method, params) as Promise<T>;
    }
  },
}));

const PROFILE = {
  description: "Reliable research companion",
  display_name: "Research Bot",
  has_avatar: true,
  is_default: false,
  model: "gateway model",
  name: "research",
  provider: "gateway provider",
};

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

function configureClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(window, "isSecureContext", {
    configurable: true,
    value: true,
  });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
}

function emitGatewayEvent(
  event: { type: string; payload?: Record<string, unknown> },
  sessionId = "runtime-canonical",
) {
  for (const handler of mocks.anyHandlers) {
    handler({ ...event, session_id: sessionId });
  }
}

async function renderChat() {
  const { default: MobilePage } = await import("./MobilePage");
  await render(
    <MemoryRouter initialEntries={["/mobile?hermes_client=android-2"]}>
      <MobilePage />
    </MemoryRouter>,
  );
  await vi.waitFor(() =>
    expect(container.querySelector("#mobile-chat-title")).not.toBeNull(),
  );
}

function clickApprovalButton(label: string) {
  const button = Array.from(
    container.querySelectorAll<HTMLButtonElement>(
      '[data-testid="mobile-approval-card"] button',
    ),
  ).find((candidate) => candidate.textContent === label);
  if (!button) throw new Error(`Approval button not found: ${label}`);
  button.click();
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocks.anyHandlers.clear();
  mocks.stateHandlers.clear();
  mocks.state = "idle";
  mocks.getActiveProfile.mockResolvedValue({ active: "research" });
  mocks.getProfiles.mockResolvedValue({ profiles: [PROFILE] });
  mocks.request.mockImplementation(async (method: string) => {
    if (method === "profiles.list") return { profiles: [PROFILE] };
    if (method === "profiles.get_asset") {
      return {
        data: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        found: true,
        mime: "image/png",
        size: 68,
      };
    }
    if (method === "session.list") return { sessions: [] };
    if (method === "session.create") {
      return { session_id: "runtime-canonical", stored_session_id: "stored-canonical" };
    }
    if (method === "session.history") return { messages: [] };
    return {};
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      addEventListener: vi.fn(),
      matches: false,
      removeEventListener: vi.fn(),
    })),
  });
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("MobilePage bot navigation", () => {
  it("shows persistent Chat/Bots tabs and opens the selected bot in Chat", async () => {
    const { default: MobilePage } = await import("./MobilePage");

    await render(
      <MemoryRouter initialEntries={["/mobile?hermes_client=android-2"]}>
        <MobilePage initialTab="bots" />
      </MemoryRouter>,
    );

    await vi.waitFor(() =>
      expect(
        container.querySelector<HTMLButtonElement>(
          'button[aria-label="Chat with Research Bot"]',
        ),
      ).not.toBeNull(),
    );

    const tabs = Array.from(
      container.querySelectorAll<HTMLButtonElement>(".mobile-console__tab"),
    );
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Chat",
      "Bots",
      "Sessions",
      "More",
    ]);
    expect(tabs[1]?.getAttribute("aria-current")).toBe("page");
    expect(container.textContent).not.toContain("Profiles");

    await vi.waitFor(() =>
      expect(
        container.querySelector<HTMLImageElement>(".mobile-profile-card__avatar-image")
          ?.src,
      ).toContain("data:image/png;base64"),
    );

    const card = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Chat with Research Bot"]',
    );
    await act(async () => card?.click());

    await vi.waitFor(() =>
      expect(container.querySelector("#mobile-chat-title")?.textContent).toBe(
        "Research Bot",
      ),
    );
    expect(tabs[0]?.getAttribute("aria-current")).toBe("page");
    expect(container.textContent).toContain("DIRECT BOT CHAT");
  });

  it("renders assistant markdown while keeping user markdown literal", async () => {
    mocks.request.mockImplementation(async (method: string) => {
      if (method === "profiles.list") return { profiles: [PROFILE] };
      if (method === "profiles.get_asset") {
        return {
          data: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
          found: true,
          mime: "image/png",
          size: 68,
        };
      }
      if (method === "session.create") {
        return { session_id: "runtime-canonical", stored_session_id: "stored-canonical" };
      }
      if (method === "session.history") {
        return {
          messages: [
            { id: "user-1", role: "user", content: "User **literal**" },
            {
              id: "assistant-1",
              role: "assistant",
              content: "**Bold** with `inline`\n\n- first\n- second",
            },
          ],
        };
      }
      return {};
    });

    const { default: MobilePage } = await import("./MobilePage");

    await render(
      <MemoryRouter initialEntries={["/mobile?hermes_client=android-2"]}>
        <MobilePage />
      </MemoryRouter>,
    );

    await vi.waitFor(() =>
      expect(container.querySelector(".mobile-message--assistant")).not.toBeNull(),
    );

    const assistant = container.querySelector<HTMLElement>(
      ".mobile-message--assistant",
    );
    expect(assistant?.querySelector("strong")?.textContent).toBe("Bold");
    expect(assistant?.querySelector("code")?.textContent).toBe("inline");
    expect(
      Array.from(assistant?.querySelectorAll("ul > li") ?? []).map(
        (item) => item.textContent,
      ),
    ).toEqual(["first", "second"]);

    const user = container.querySelector<HTMLElement>(".mobile-message--user");
    expect(user?.querySelector("p")?.textContent).toBe("User **literal**");
    expect(user?.querySelector("strong")).toBeNull();
    expect(user?.querySelector("code")).toBeNull();
    expect(user?.querySelector("ul")).toBeNull();
    expect(user?.querySelector('button[aria-label="Copy response"]')).toBeNull();

    const writeText = vi.fn().mockResolvedValue(undefined);
    configureClipboard(writeText);
    const copyButton = assistant?.querySelector<HTMLButtonElement>(
      'button[aria-label="Copy response"]',
    );
    expect(copyButton?.title).toBe("Copy response");
    await act(async () => copyButton?.click());
    await vi.waitFor(() =>
      expect(
        assistant?.querySelector(".mobile-message__copy-feedback")?.textContent,
      ).toBe("Copied"),
    );
    expect(writeText).toHaveBeenCalledWith(
      "**Bold** with `inline`\n\n- first\n- second",
    );
  });

  it("falls back to legacy copy when the Clipboard API rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("not allowed"));
    const execCommand = vi.fn().mockReturnValue(true);
    configureClipboard(writeText);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });
    mocks.request.mockImplementation(async (method: string) => {
      if (method === "profiles.list") return { profiles: [PROFILE] };
      if (method === "profiles.get_asset") return {};
      if (method === "session.create") {
        return { session_id: "runtime-canonical", stored_session_id: "stored-canonical" };
      }
      if (method === "session.history") {
        return {
          messages: [{ id: "assistant-1", role: "assistant", content: "Raw reply" }],
        };
      }
      return {};
    });

    const { default: MobilePage } = await import("./MobilePage");
    await render(
      <MemoryRouter initialEntries={["/mobile?hermes_client=android-2"]}>
        <MobilePage />
      </MemoryRouter>,
    );

    await vi.waitFor(() =>
      expect(container.querySelector('button[aria-label="Copy response"]')).not.toBeNull(),
    );
    const copyButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Copy response"]',
    );
    await act(async () => copyButton?.click());
    await vi.waitFor(() =>
      expect(container.querySelector(".mobile-message__copy-feedback")?.textContent).toBe(
        "Copied",
      ),
    );
    expect(writeText).toHaveBeenCalledWith("Raw reply");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("hides copy controls for empty, streaming, and error responses", async () => {
    mocks.request.mockImplementation(async (method: string) => {
      if (method === "profiles.list") return { profiles: [PROFILE] };
      if (method === "profiles.get_asset") return {};
      if (method === "session.create") {
        return { session_id: "runtime-canonical", stored_session_id: "stored-canonical" };
      }
      if (method === "session.history") {
        return { messages: [{ id: "empty-1", role: "assistant", content: "   " }] };
      }
      return {};
    });

    const { default: MobilePage } = await import("./MobilePage");
    await render(
      <MemoryRouter initialEntries={["/mobile?hermes_client=android-2"]}>
        <MobilePage />
      </MemoryRouter>,
    );

    await vi.waitFor(() => expect(mocks.anyHandlers.size).toBeGreaterThan(0));
    expect(container.querySelector('button[aria-label="Copy response"]')).toBeNull();

    const emit = (event: { type: string; payload?: Record<string, unknown> }) => {
      for (const handler of mocks.anyHandlers) {
        handler({ ...event, session_id: "runtime-canonical" });
      }
    };
    await act(async () => emit({ type: "message.delta", payload: { text: "partial" } }));
    await vi.waitFor(() =>
      expect(container.querySelector(".mobile-message--assistant")).not.toBeNull(),
    );
    expect(container.querySelector('button[aria-label="Copy response"]')).toBeNull();

    await act(async () =>
      emit({
        type: "message.complete",
        payload: { text: "failed", error: "gateway failure" },
      }),
    );
    expect(container.querySelector('button[aria-label="Copy response"]')).toBeNull();
  });
});

describe("MobilePage command approvals", () => {
  async function waitForBoundSession() {
    await vi.waitFor(() =>
      expect(
        mocks.request.mock.calls.some(([method]) => method === "session.history"),
      ).toBe(true),
    );
  }

  it("renders only the active-session request and acknowledges it with the selected profile", async () => {
    await renderChat();
    await waitForBoundSession();
    mocks.request.mockClear();
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    await act(async () =>
      emitGatewayEvent(
        {
          type: "approval.request",
          payload: {
            allow_permanent: true,
            choices: ["once", "session", "always", "deny", "unknown"],
            command: "rm -rf /tmp/demo",
            description: "Remove the demo directory",
            request_id: "approval-1",
          },
        },
        "background-session",
      ),
    );
    expect(container.querySelector('[data-testid="mobile-approval-card"]')).toBeNull();

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          allow_permanent: true,
          choices: ["once", "session", "always", "deny", "unknown"],
          command: "rm -rf /tmp/demo",
          description: "Remove the demo directory",
          request_id: "approval-1",
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    await vi.waitFor(() =>
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: "smooth",
        block: "center",
      }),
    );

    const card = container.querySelector<HTMLElement>('[data-testid="mobile-approval-card"]');
    expect(card?.textContent).toContain("Remove the demo directory");
    expect(card?.textContent).toContain("rm -rf /tmp/demo");
    expect(card?.textContent).toContain("Run once");
    expect(card?.textContent).toContain("Allow for session");
    expect(card?.textContent).toContain("Always allow");
    expect(card?.textContent).toContain("Reject");
    expect(
      container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message Research Bot"]')
        ?.disabled,
    ).toBe(true);
    expect(container.querySelector('button[aria-label="Stop response"]')).not.toBeNull();
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("approval.received", {
        profile: "research",
        request_id: "approval-1",
        session_id: "runtime-canonical",
      }),
    );
  });

  it("renders and accepts only the choices offered by the gateway", async () => {
    await renderChat();
    await waitForBoundSession();
    mocks.request.mockClear();

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "echo deny",
          description: "Deny-only request",
          request_id: "approval-deny-only",
          choices: ["deny"],
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    expect(container.textContent).toContain("Reject");
    expect(container.textContent).not.toContain("Run once");
    expect(container.textContent).not.toContain("Allow for session");
    expect(container.textContent).not.toContain("Always allow");
    await act(async () => {
      clickApprovalButton("Reject");
    });
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("approval.respond", {
        choice: "deny",
        profile: "research",
        request_id: "approval-deny-only",
        session_id: "runtime-canonical",
      }),
    );
    expect(mocks.request).not.toHaveBeenCalledWith(
      "approval.respond",
      expect.objectContaining({ choice: "once" }),
    );

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "echo once",
          description: "Once-only request",
          request_id: "approval-once-only",
          choices: ["once"],
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    expect(container.textContent).toContain("Run once");
    expect(container.textContent).not.toContain("Reject");
    expect(container.textContent).not.toContain("Allow for session");
    expect(container.textContent).not.toContain("Always allow");
    await act(async () => {
      clickApprovalButton("Run once");
    });
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("approval.respond", {
        choice: "once",
        profile: "research",
        request_id: "approval-once-only",
        session_id: "runtime-canonical",
      }),
    );
    expect(mocks.request).not.toHaveBeenCalledWith(
      "approval.respond",
      expect.objectContaining({
        choice: "deny",
        request_id: "approval-once-only",
      }),
    );
  });

  it("responds to Run once and Reject with session, request, and profile routing", async () => {
    await renderChat();
    await waitForBoundSession();
    mocks.request.mockClear();

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "touch /tmp/once",
          description: "Create a marker",
          request_id: "approval-once",
          choices: ["once", "deny"],
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    await act(async () => {
      clickApprovalButton("Run once");
    });
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("approval.respond", {
        choice: "once",
        profile: "research",
        request_id: "approval-once",
        session_id: "runtime-canonical",
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).toBeNull(),
    );

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "touch /tmp/deny",
          description: "Create another marker",
          request_id: "approval-deny",
          choices: ["once", "deny"],
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    await act(async () => {
      clickApprovalButton("Reject");
    });
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("approval.respond", {
        choice: "deny",
        profile: "research",
        request_id: "approval-deny",
        session_id: "runtime-canonical",
      }),
    );
  });

  it("requires explicit confirmation for permanent approval and hides disallowed choices", async () => {
    await renderChat();
    await waitForBoundSession();
    mocks.request.mockClear();

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          allow_permanent: true,
          choices: ["once", "session", "always", "deny"],
          command: "chmod 700 /tmp/demo",
          description: "Change demo permissions",
          request_id: "approval-always",
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    await act(async () => {
      clickApprovalButton("Always allow");
    });
    expect(mocks.request).not.toHaveBeenCalledWith(
      "approval.respond",
      expect.objectContaining({ choice: "always" }),
    );
    expect(container.textContent).toContain("Always allow this command?");
    await vi.waitFor(() =>
      expect(document.activeElement?.textContent).toBe("Confirm always"),
    );
    await act(async () => {
      clickApprovalButton("Confirm always");
    });
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("approval.respond", {
        choice: "always",
        profile: "research",
        request_id: "approval-always",
        session_id: "runtime-canonical",
      }),
    );

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          allow_permanent: true,
          choices: ["once", "session", "always", "deny"],
          command: "chmod 700 /tmp/smart",
          description: "Smart denied command",
          request_id: "approval-smart",
          smart_denied: true,
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    expect(container.textContent).not.toContain("Allow for session");
    expect(container.textContent).not.toContain("Always allow");
  });

  it("keeps the card when approval RPC fails", async () => {
    await renderChat();
    await waitForBoundSession();
    let responseAttempts = 0;
    mocks.request.mockImplementation(async (method: string) => {
      if (method === "approval.respond") {
        responseAttempts += 1;
        if (responseAttempts === 1) throw new Error("approval RPC failed");
      }
      return {};
    });

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "echo live",
          description: "Live request",
          request_id: "approval-live",
          choices: ["once", "deny"],
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    await act(async () => {
      clickApprovalButton("Run once");
    });
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-error"]')?.textContent).toBe(
        "approval RPC failed",
      ),
    );
    expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull();
    expect(
      container.querySelector<HTMLButtonElement>(
        '[data-testid="mobile-approval-card"] button',
      )?.disabled,
    ).toBe(false);

    await act(async () => {
      clickApprovalButton("Run once");
    });
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).toBeNull(),
    );
    expect(container.querySelector('[data-testid="mobile-approval-error"]')).toBeNull();
  });

  it("clears approval failure text when a different request replaces it", async () => {
    await renderChat();
    await waitForBoundSession();
    mocks.request.mockImplementation(async (method: string) => {
      if (method === "approval.respond") throw new Error("approval RPC failed");
      return {};
    });

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "echo first",
          description: "First request",
          request_id: "approval-first",
          choices: ["once"],
        },
      }),
    );
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-card"]')).not.toBeNull(),
    );
    await act(async () => {
      clickApprovalButton("Run once");
    });
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="mobile-approval-error"]')).not.toBeNull(),
    );

    await act(async () =>
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "echo second",
          description: "Second request",
          request_id: "approval-second",
          choices: ["once"],
        },
      }),
    );
    await vi.waitFor(() => expect(container.textContent).toContain("Second request"));
    expect(container.querySelector('[data-testid="mobile-approval-error"]')).toBeNull();
  });

  it("restores pending approvals after reconnect without replacing a newer live request", async () => {
    await renderChat();
    await waitForBoundSession();
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    mocks.request.mockImplementation(async (method: string) => {
      if (method === "approval.pending") {
        return {
          approvals: [
            {
              command: "echo replay",
              description: "Replayed request",
              request_id: "approval-replay",
              choices: ["once", "deny"],
            },
          ],
        };
      }
      return {};
    });
    for (const handler of mocks.stateHandlers) {
      await act(async () => handler("open"));
    }
    await vi.waitFor(() => expect(container.textContent).toContain("Replayed request"));
    await vi.waitFor(() =>
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: "smooth",
        block: "center",
      }),
    );
    expect(mocks.request).toHaveBeenCalledWith("approval.pending", {
      profile: "research",
      session_id: "runtime-canonical",
    });

    mocks.request.mockImplementation(async () => ({ approvals: [] }));
    await act(async () => {
      for (const handler of mocks.stateHandlers) handler("open");
      emitGatewayEvent({
        type: "approval.request",
        payload: {
          command: "echo newer",
          description: "Newer live request",
          request_id: "approval-newer",
          choices: ["once", "deny"],
        },
      });
    });
    await vi.waitFor(() => expect(container.textContent).toContain("Newer live request"));
    expect(container.textContent).not.toContain("Replayed request");
  });

  it("invalidates the old runtime before a delayed session resume", async () => {
    let resolveResume: ((response: Record<string, unknown>) => void) | undefined;
    const delayedResume = new Promise<Record<string, unknown>>((resolve) => {
      resolveResume = resolve;
    });
    mocks.request.mockImplementation(
      async (method: string, params: Record<string, unknown>) => {
        if (method === "profiles.list") return { profiles: [PROFILE] };
        if (method === "profiles.get_asset") return {};
        if (method === "session.list") {
          return {
            sessions: [
              {
                id: "stored-target",
                message_count: 0,
                preview: "Target session",
                started_at: 1,
                title: "Target session",
              },
            ],
          };
        }
        if (method === "session.create") {
          return { session_id: "runtime-canonical", stored_session_id: "stored-canonical" };
        }
        if (method === "session.resume" && params.session_id === "stored-target") {
          return delayedResume;
        }
        if (method === "session.resume") throw new Error("canonical session missing");
        if (method === "session.history") return { messages: [] };
        return {};
      },
    );

    await renderChat();
    await waitForBoundSession();
    const sessionsTab = Array.from(
      container.querySelectorAll<HTMLButtonElement>(".mobile-console__tab"),
    ).find((button) => button.textContent === "Sessions");
    await act(async () => {
      sessionsTab?.click();
    });
    await vi.waitFor(() =>
      expect(container.querySelector<HTMLButtonElement>(".mobile-session-card")).not.toBeNull(),
    );

    await act(async () => {
      container.querySelector<HTMLButtonElement>(".mobile-session-card")?.click();
    });
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("session.resume", {
        profile: "research",
        session_id: "stored-target",
        source: "mobile",
      }),
    );

    await act(async () =>
      emitGatewayEvent(
        {
          type: "approval.request",
          payload: {
            command: "echo old runtime",
            description: "Must stay hidden",
            request_id: "approval-old-runtime",
            choices: ["once", "deny"],
          },
        },
        "runtime-canonical",
      ),
    );
    expect(container.querySelector('[data-testid="mobile-approval-card"]')).toBeNull();

    await act(async () => {
      resolveResume?.({
        resumed: "stored-target",
        session_id: "runtime-target",
      });
      await Promise.resolve();
    });
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith("session.history", {
        session_id: "runtime-target",
      }),
    );
    expect(container.querySelector('[data-testid="mobile-approval-card"]')).toBeNull();
  });
});
