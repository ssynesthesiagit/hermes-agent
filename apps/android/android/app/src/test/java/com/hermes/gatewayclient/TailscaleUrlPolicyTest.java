package com.hermes.gatewayclient;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

public class TailscaleUrlPolicyTest {

    @Test
    public void acceptsTailnetAddressesAndPreservesPortAndPath() {
        String ipv4 = "http://100.112.52.74:9119/chat/session?id=7#view";
        String ipv6 = "https://[fd7a:115c:a1e0::42]:443/sessions";
        String dns = "https://worker-name.ts.net:9443/a/b?next=%2Fhome";

        assertEquals(ipv4, TailscaleUrlPolicy.validateAndNormalize(ipv4));
        assertEquals(ipv6, TailscaleUrlPolicy.validateAndNormalize(ipv6));
        assertEquals(dns, TailscaleUrlPolicy.validateAndNormalize(dns));
    }

    @Test
    public void acceptsTheEntireTailnetIpv4Range() {
        assertTrue(TailscaleUrlPolicy.isAllowed("http://100.64.0.0"));
        assertTrue(TailscaleUrlPolicy.isAllowed("http://100.127.255.255"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://100.63.255.255"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://100.128.0.0"));
    }

    @Test
    public void acceptsOnlyTheConfiguredTailnetIpv6Prefix() {
        assertTrue(TailscaleUrlPolicy.isAllowed("http://[fd7a:115c:a1e0::1]"));
        assertTrue(TailscaleUrlPolicy.isAllowed("http://[FD7A:115C:A1E0:0:0:0:0:1]"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://[fd7a:115c:a1e1::1]"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://[fe80::1]"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://fd7a:115c:a1e0::1"));
    }

    @Test
    public void rejectsUnsafeOrAmbiguousHostsAndSchemes() {
        assertFalse(TailscaleUrlPolicy.isAllowed("file:///data/local"));
        assertFalse(TailscaleUrlPolicy.isAllowed("javascript:alert(1)"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://user:password@worker.ts.net"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://worker.ts.net.evil.example"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://ts.net"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://100.112.52.074"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://192.168.1.5"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://worker.ts.net:99999"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://worker.ts.net:bad"));
        assertFalse(TailscaleUrlPolicy.isAllowed(" http://worker.ts.net"));
        assertFalse(TailscaleUrlPolicy.isAllowed("http://worker..ts.net"));
        assertNull(TailscaleUrlPolicy.validateAndNormalize(null));
    }
}
