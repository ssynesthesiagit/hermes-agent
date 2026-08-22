export function getRootRedirectTarget(isMobile: boolean): "/mobile" | "/sessions" {
  return isMobile ? "/mobile" : "/sessions";
}

export type ProfileRouteTarget = "mobile-bots" | "profiles";
export type MobileInitialTab = "chat" | "bots" | "sessions" | "more";

export function getProfileRouteTarget(
  pathname: string,
  search: string,
  isMobile: boolean,
): ProfileRouteTarget | null {
  const normalizedPath = pathname.replace(/\/+$/, "") || "/";
  if (normalizedPath !== "/profiles") return null;

  const manageView = new URLSearchParams(search).get("view") === "manage";
  return isMobile && !manageView ? "mobile-bots" : "profiles";
}

export function resolveInitialMobileTab(
  savedTab: MobileInitialTab,
  forcedTab?: MobileInitialTab,
): MobileInitialTab {
  return forcedTab ?? savedTab;
}
