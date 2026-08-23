package com.hermes.gatewayclient;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class TailscaleEgressGuardTest {

    @Test
    public void unsupportedDocumentStartCannotLoadGateway() {
        assertFalse(TailscaleEgressGuard.canLoadGateway(false));
        assertTrue(TailscaleEgressGuard.canLoadGateway(true));
    }

    @Test
    public void scriptGuardsRemoteTransportAndWorkerEscapeHatches() {
        String script = TailscaleEgressGuard.script();

        assertTrue(script.contains("fetch"));
        assertTrue(script.contains("XMLHttpRequest"));
        assertTrue(script.contains("WebSocket"));
        assertTrue(script.contains("EventSource"));
        assertTrue(script.contains("sendBeacon"));
        assertTrue(script.contains("WebTransport"));
        assertTrue(script.contains("Worker"));
        assertTrue(script.contains("SharedWorker"));
        assertTrue(script.contains("RTCPeerConnection"));
        assertTrue(script.contains("serviceWorker"));
        assertTrue(script.contains("configurable: false"));
    }

    @Test
    public void scriptContainsTheSameTailnetHostFamilies() {
        String script = TailscaleEgressGuard.script();

        assertTrue(script.contains("100.64.0.0"));
        assertTrue(script.contains("fd7a:115c:a1e0"));
        assertTrue(script.contains(".ts.net"));
        assertTrue(script.contains("['ws:', 'wss:']"));
    }
}
