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
