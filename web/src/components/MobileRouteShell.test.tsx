// @vitest-environment jsdom
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  MemoryRouter,
  useLocation,
  useNavigate,
} from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MobilePage from "@/pages/MobilePage";
import { getProfileRouteTarget } from "@/lib/root-redirect";
import { MobileRouteShell } from "./MobileRouteShell";

const MOBILE_TAB_KEY = "hermes.mobile.selected-tab";

const gatewayMocks = vi.hoisted(() => ({
  close: vi.fn(),
  connect: vi.fn(async () => undefined),
  onAny: vi.fn(() => () => undefined),
  onState: vi.fn(() => () => undefined),
  request: vi.fn(async () => ({})),
}));

const apiMocks = vi.hoisted(() => ({
  getActiveProfile: vi.fn(async () => ({ active: "" })),
  getProfiles: vi.fn(async () => ({ profiles: [] })),
}));

vi.mock("@/lib/gatewayClient", () => ({
  GatewayClient: class {
    connectionState = "closed";
    close = gatewayMocks.close;
    connect = gatewayMocks.connect;
    onAny = gatewayMocks.onAny;
    onState = gatewayMocks.onState;
    request = gatewayMocks.request;
  },
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | undefined;
let root: Root | undefined;

function RouteHarness() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const isPhoneProfilesAlias =
    getProfileRouteTarget(pathname, search, true) === "mobile-bots";

  return createElement(
    "div",
    null,
    createElement(MobileRouteShell, {
      isPhoneProfilesAlias,
      mobileElement: createElement(MobilePage),
      mobilePage: MobilePage,
    }),
    createElement(
      "button",
      {
        type: "button",
        "data-testid": "go-profiles",
        onClick: () => void navigate("/profiles"),
      },
      "Profiles",
    ),
    createElement(
      "button",
      {
        type: "button",
        "data-testid": "go-mobile",
        onClick: () => void navigate("/mobile"),
      },
      "Mobile",
    ),
  );
}

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(ui));
}

function selectedTab() {
  const button = container?.querySelector<HTMLButtonElement>(
    '[aria-current="page"]',
  );
  if (!button) throw new Error("Expected a selected mobile tab");
  return button.textContent?.trim();
}

async function click(testId: string) {
  const button = container?.querySelector<HTMLButtonElement>(
    `[data-testid="${testId}"]`,
  );
  if (!button) throw new Error(`Expected ${testId}`);
  await act(async () => button.click());
}

async function clickTab(label: string) {
  const button = [...(container?.querySelectorAll<HTMLButtonElement>(
    ".mobile-console__tab",
  ) ?? [])].find((candidate) => candidate.textContent?.trim() === label);
  if (!button) throw new Error(`Expected the ${label} tab`);
  await act(async () => button.click());
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(MOBILE_TAB_KEY, "sessions");
  gatewayMocks.close.mockClear();
  gatewayMocks.connect.mockClear();
  gatewayMocks.onAny.mockClear();
  gatewayMocks.onState.mockClear();
  gatewayMocks.request.mockClear();
  apiMocks.getActiveProfile.mockClear();
  apiMocks.getProfiles.mockClear();
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("MobileRouteShell", () => {
  it("resets the alias to Bots and restores /mobile's saved tab across navigation", async () => {
    await render(
      <MemoryRouter initialEntries={["/mobile"]}>
        <RouteHarness />
      </MemoryRouter>,
    );

    expect(selectedTab()).toBe("Sessions");

    await click("go-profiles");
    expect(selectedTab()).toBe("Bots");

    await click("go-mobile");
    expect(selectedTab()).toBe("Sessions");
  });

  it("keeps tab clicks functional within the alias route", async () => {
    await render(
      <MemoryRouter initialEntries={["/profiles/"]}>
        <RouteHarness />
      </MemoryRouter>,
    );

    expect(selectedTab()).toBe("Bots");
    await clickTab("More");
    expect(selectedTab()).toBe("More");
  });
});
