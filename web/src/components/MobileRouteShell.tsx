import type { ReactNode } from "react";
import { cloneElement, isValidElement } from "react";
import { Route, Routes } from "react-router";

import type { MobileInitialTab } from "@/lib/root-redirect";

type MobilePageProps = { initialTab?: MobileInitialTab };
type MobilePageComponent = (props: MobilePageProps) => ReactNode;

export interface MobileRouteShellProps {
  isPhoneProfilesAlias: boolean;
  mobileElement: ReactNode;
  mobilePage: MobilePageComponent;
}

/**
 * Keep the phone console's React identity tied to the URL surface that owns it.
 * The alias and /mobile intentionally share a page implementation, but they
 * do not share tab state or initial-tab semantics.
 */
export function MobileRouteShell({
  isPhoneProfilesAlias,
  mobileElement,
  mobilePage: MobilePage,
}: MobileRouteShellProps) {
  const routePath = isPhoneProfilesAlias ? "/profiles" : "/mobile";
  const routeKey = isPhoneProfilesAlias ? "profiles-bots" : "mobile";
  const page = isPhoneProfilesAlias ? (
    <MobilePage key={routeKey} initialTab="bots" />
  ) : isValidElement(mobileElement) ? (
    cloneElement(mobileElement, { key: routeKey })
  ) : (
    mobileElement
  );

  return (
    <Routes>
      <Route path={routePath} element={page} />
    </Routes>
  );
}
