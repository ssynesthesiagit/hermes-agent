// @vitest-environment jsdom
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useNavigationType,
} from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RootRedirect } from "@/App";
import {
  getProfileRouteTarget,
  getRootRedirectTarget,
  resolveInitialMobileTab,
} from "./root-redirect";

const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | undefined;
let root: Root | undefined;

function stubMatchMedia(width: number) {
  vi.stubGlobal("matchMedia", (query: string): MediaQueryList => ({
    matches: query === "(max-width: 1023px)" && width < 1024,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

function RouteMarker({ label }: { label: string }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const navigationType = useNavigationType();
  return createElement(
    "div",
    {
      "data-testid": "route-marker",
      "data-route": pathname,
      "data-navigation-type": navigationType,
    },
    createElement("span", null, label),
    createElement(
      "button",
      { type: "button", "data-testid": "back", onClick: () => void navigate(-1) },
      "Back",
    ),
  );
}

function createRootRedirectRoutes(initialEntries: string[], initialIndex: number) {
  return createElement(
    MemoryRouter,
    { initialEntries, initialIndex },
    createElement(
      Routes,
      null,
      createElement(Route, { path: "/", element: createElement(RootRedirect) }),
      createElement(Route, {
        path: "/mobile",
        element: createElement(RouteMarker, { label: "mobile" }),
      }),
      createElement(Route, {
        path: "/sessions",
        element: createElement(RouteMarker, { label: "sessions" }),
      }),
      createElement(Route, {
        path: "/profiles",
        element: createElement(RouteMarker, { label: "profiles" }),
      }),
    ),
  );
}

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(ui));
}

function currentRoute() {
  const marker = container?.querySelector<HTMLElement>(
    '[data-testid="route-marker"]',
  );
  if (!marker) throw new Error("Expected a rendered route marker");
  return marker;
}

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
  vi.unstubAllGlobals();
});

describe("getRootRedirectTarget", () => {
  it("sends narrow root visits to the mobile console", () => {
    expect(getRootRedirectTarget(true)).toBe("/mobile");
  });

  it("preserves the desktop root destination", () => {
    expect(getRootRedirectTarget(false)).toBe("/sessions");
  });
});

describe("getProfileRouteTarget", () => {
  it.each([
    ["/profiles", "", true, "mobile-bots"],
    ["/profiles/", "", true, "mobile-bots"],
    ["/profiles", "", false, "profiles"],
    ["/profiles/", "", false, "profiles"],
    ["/profiles", "?view=manage", true, "profiles"],
    ["/profiles", "?view=manage", false, "profiles"],
    ["/profiles/new", "", true, null],
    ["/mobile", "", true, null],
    ["/mobile", "", false, null],
  ] as const)(
    "maps %s%s at mobile=%s to %s",
    (pathname, search, isMobile, target) => {
      expect(getProfileRouteTarget(pathname, search, isMobile)).toBe(target);
    },
  );
});

describe("resolveInitialMobileTab", () => {
  it("keeps a forced Bots tab ahead of the saved preference", () => {
    expect(resolveInitialMobileTab("sessions", "bots")).toBe("bots");
  });
});

describe("RootRedirect router integration", () => {
  it.each([
    [1023, "/mobile"],
    [1024, "/sessions"],
  ] as const)(
    "redirects / at %dpx to %s and replaces the history entry",
    async (width, target) => {
      stubMatchMedia(width);
      await render(createRootRedirectRoutes(["/profiles", "/"], 1));

      expect(currentRoute().getAttribute("data-route")).toBe(target);
      expect(currentRoute().getAttribute("data-navigation-type")).toBe("REPLACE");

      const backButton = currentRoute().querySelector<HTMLButtonElement>(
        '[data-testid="back"]',
      );
      if (!backButton) throw new Error("Expected a back button");
      await act(async () => backButton.click());
      expect(currentRoute().getAttribute("data-route")).toBe("/profiles");
    },
  );

  it.each([1023, 1024] as const)(
    "does not redirect an explicit /profiles location at %dpx",
    async (width) => {
      stubMatchMedia(width);
      await render(createRootRedirectRoutes(["/profiles"], 0));

      expect(currentRoute().getAttribute("data-route")).toBe("/profiles");
      expect(currentRoute().textContent).toContain("profiles");
    },
  );
});
