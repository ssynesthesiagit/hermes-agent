package com.hermes.gatewayclient;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class GatewayMobileUrlTest {

    private static final int CLIENT_REVISION = 2;

    @Test
    public void appendsMobileRouteToRoot() {
        assertEquals(
            "https://worker-name.ts.net/mobile?hermes_client=android-2",
            GatewayMobileUrl.toMobileUrl("https://worker-name.ts.net", CLIENT_REVISION)
        );
    }

    @Test
    public void appendsMobileRouteAfterTrailingSlash() {
        assertEquals(
            "http://100.112.52.74:9119/mobile?hermes_client=android-2",
            GatewayMobileUrl.toMobileUrl("http://100.112.52.74:9119/", CLIENT_REVISION)
        );
    }

    @Test
    public void appendsMobileRouteToReverseProxyPath() {
        assertEquals(
            "https://worker-name.ts.net:9443/hermes/gateway/mobile?hermes_client=android-2",
            GatewayMobileUrl.toMobileUrl("https://worker-name.ts.net:9443/hermes/gateway/", CLIENT_REVISION)
        );
    }

    @Test
    public void doesNotDuplicateExistingMobileRoute() {
        String mobileUrl = "https://worker-name.ts.net/hermes/mobile";

        assertEquals(
            mobileUrl + "?hermes_client=android-2",
            GatewayMobileUrl.toMobileUrl(mobileUrl, CLIENT_REVISION)
        );
        assertEquals(
            mobileUrl + "?hermes_client=android-2",
            GatewayMobileUrl.toMobileUrl(mobileUrl + "/", CLIENT_REVISION)
        );
    }

    @Test
    public void preservesQueryAndFragmentAfterRoutePath() {
        assertEquals(
            "https://worker-name.ts.net/hermes/mobile?next=%2Fhome&mode=compact&hermes_client=android-2#overview",
            GatewayMobileUrl.toMobileUrl(
                "https://worker-name.ts.net/hermes?next=%2Fhome&mode=compact#overview",
                CLIENT_REVISION
            )
        );
    }

    @Test
    public void replacesExistingHermesClientQueryItem() {
        assertEquals(
            "https://worker-name.ts.net/hermes/mobile?next=%2Fhome&hermes_client_extra=keep&mode=compact&hermes_client=android-2",
            GatewayMobileUrl.toMobileUrl(
                "https://worker-name.ts.net/hermes?next=%2Fhome&hermes_client=android-1&hermes_client_extra=keep&mode=compact",
                CLIENT_REVISION
            )
        );
    }

    @Test
    public void removesRepeatedHermesClientQueryItemsBeforeFragment() {
        assertEquals(
            "https://worker-name.ts.net/hermes/mobile?mode=compact&region=us&hermes_client=android-2#overview",
            GatewayMobileUrl.toMobileUrl(
                "https://worker-name.ts.net/hermes?hermes_client=stale&mode=compact&hermes_client=old&region=us#overview",
                CLIENT_REVISION
            )
        );
    }

    @Test
    public void discardsEmptyQuerySegmentsAndCanonicalizesSeparators() {
        assertEquals(
            "https://worker-name.ts.net/hermes/mobile?mode=compact&hermes_client_extra=keep&region=us&hermes_client=android-2#overview",
            GatewayMobileUrl.toMobileUrl(
                "https://worker-name.ts.net/hermes?&&mode=compact&&hermes_client=old&&hermes_client_extra=keep&region=us&&#overview",
                CLIENT_REVISION
            )
        );
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsInvalidRevision() {
        GatewayMobileUrl.toMobileUrl("https://worker-name.ts.net", 0);
    }

    @Test
    public void identifiesOnlyRenderedMobileRoutes() {
        assertTrue(GatewayMobileUrl.isMobileRoute("http://100.97.133.41:9119/mobile?hermes_client=android-4"));
        assertTrue(GatewayMobileUrl.isMobileRoute("https://worker-name.ts.net/hermes/mobile/"));
        assertFalse(GatewayMobileUrl.isMobileRoute(
            "http://100.97.133.41:9119/login?next=%2Fmobile%3Fhermes_client%3Dandroid-4"
        ));
        assertFalse(GatewayMobileUrl.isMobileRoute("not a url"));
    }
}
