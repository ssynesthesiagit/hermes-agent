package com.hermes.gatewayclient;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Accepts only HTTP(S) URLs that address a Tailscale CGNAT/ULA address or a
 * proper Tailscale DNS name. This class deliberately does not resolve DNS.
 */
public final class TailscaleUrlPolicy {

    private static final Pattern HOST_LABEL = Pattern.compile("[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?");

    private TailscaleUrlPolicy() {}

    /**
     * Returns the original URL when it is safe to use as a gateway URL, or
     * null when it is malformed or outside the Tailscale address space.
     */
    public static String validateAndNormalize(String value) {
        if (value == null || value.isEmpty() || !value.equals(value.trim())) {
            return null;
        }

        final URI uri;
        try {
            uri = new URI(value);
        } catch (URISyntaxException ex) {
            return null;
        }

        String scheme = uri.getScheme();
        if (scheme == null || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))) {
            return null;
        }
        if (uri.getRawAuthority() == null || uri.getRawAuthority().isEmpty()) {
            return null;
        }
        if (uri.getUserInfo() != null || uri.getRawUserInfo() != null) {
            return null;
        }

        String rawAuthority = uri.getRawAuthority();
        if (rawAuthority.indexOf('@') >= 0 || rawAuthority.indexOf('%') >= 0) {
            return null;
        }

        Authority authority = Authority.parse(rawAuthority);
        if (authority == null || !isAllowedHost(authority.host)) {
            return null;
        }

        // URI has already validated the path/query/fragment syntax. Return the
        // original spelling so a valid port and path are not rewritten.
        return value;
    }

    public static boolean isAllowed(String value) {
        return validateAndNormalize(value) != null;
    }

    private static boolean isAllowedHost(String host) {
        return isAllowedIpv4(host) || isAllowedIpv6(host) || isProperTsNetHost(host);
    }

    private static boolean isAllowedIpv4(String host) {
        String[] octets = host.split("\\.", -1);
        if (octets.length != 4) {
            return false;
        }

        int[] parsed = new int[4];
        for (int index = 0; index < octets.length; index++) {
            String octet = octets[index];
            if (octet.isEmpty() || octet.length() > 3 || (octet.length() > 1 && octet.charAt(0) == '0')) {
                return false;
            }
            int number = 0;
            for (int character = 0; character < octet.length(); character++) {
                char digit = octet.charAt(character);
                if (digit < '0' || digit > '9') {
                    return false;
                }
                number = number * 10 + (digit - '0');
            }
            if (number > 255) {
                return false;
            }
            parsed[index] = number;
        }

        return parsed[0] == 100 && parsed[1] >= 64 && parsed[1] <= 127;
    }

    private static boolean isAllowedIpv6(String host) {
        if (host.indexOf(':') < 0 || host.indexOf('.') >= 0) {
            return false;
        }

        int[] groups = new int[8];
        int doubleColon = host.indexOf("::");
        if (doubleColon >= 0) {
            if (host.indexOf("::", doubleColon + 2) >= 0) {
                return false;
            }

            String left = host.substring(0, doubleColon);
            String right = host.substring(doubleColon + 2);
            int[] leftGroups = parseHextets(left);
            int[] rightGroups = parseHextets(right);
            if (leftGroups == null || rightGroups == null || leftGroups.length + rightGroups.length >= 8) {
                return false;
            }

            System.arraycopy(leftGroups, 0, groups, 0, leftGroups.length);
            int rightStart = groups.length - rightGroups.length;
            System.arraycopy(rightGroups, 0, groups, rightStart, rightGroups.length);
        } else {
            int[] parsed = parseHextets(host);
            if (parsed == null || parsed.length != 8) {
                return false;
            }
            groups = parsed;
        }

        return groups[0] == 0xfd7a && groups[1] == 0x115c && groups[2] == 0xa1e0;
    }

    private static int[] parseHextets(String value) {
        if (value.isEmpty()) {
            return new int[0];
        }

        String[] values = value.split(":", -1);
        int[] groups = new int[values.length];
        for (int index = 0; index < values.length; index++) {
            String group = values[index];
            if (group.isEmpty() || group.length() > 4) {
                return null;
            }
            int parsed = 0;
            for (int character = 0; character < group.length(); character++) {
                int digit = Character.digit(group.charAt(character), 16);
                if (digit < 0) {
                    return null;
                }
                parsed = (parsed << 4) | digit;
            }
            groups[index] = parsed;
        }
        return groups;
    }

    private static boolean isProperTsNetHost(String host) {
        String lowerHost = host.toLowerCase(Locale.ROOT);
        if (!lowerHost.endsWith(".ts.net") || lowerHost.length() <= ".ts.net".length() || host.length() > 253) {
            return false;
        }

        String[] labels = host.split("\\.", -1);
        if (labels.length < 3) {
            return false;
        }
        for (String label : labels) {
            if (!HOST_LABEL.matcher(label).matches()) {
                return false;
            }
        }
        return true;
    }

    private static final class Authority {
        private final String host;

        private Authority(String host) {
            this.host = host;
        }

        private static Authority parse(String rawAuthority) {
            if (rawAuthority.startsWith("[")) {
                int close = rawAuthority.indexOf(']');
                if (close <= 1) {
                    return null;
                }
                String host = rawAuthority.substring(1, close);
                String remainder = rawAuthority.substring(close + 1);
                if (!remainder.isEmpty() && !remainder.startsWith(":")) {
                    return null;
                }
                return remainder.isEmpty() || isValidPort(remainder.substring(1)) ? new Authority(host) : null;
            }

            int colon = rawAuthority.indexOf(':');
            if (colon >= 0 && colon != rawAuthority.lastIndexOf(':')) {
                // IPv6 literals must be bracketed, avoiding ambiguous ports.
                return null;
            }
            if (colon < 0) {
                return rawAuthority.isEmpty() ? null : new Authority(rawAuthority);
            }

            String host = rawAuthority.substring(0, colon);
            String port = rawAuthority.substring(colon + 1);
            return host.isEmpty() || !isValidPort(port) ? null : new Authority(host);
        }

        private static boolean isValidPort(String value) {
            if (value.isEmpty() || value.length() > 5) {
                return false;
            }
            int port = 0;
            for (int index = 0; index < value.length(); index++) {
                char digit = value.charAt(index);
                if (digit < '0' || digit > '9') {
                    return false;
                }
                port = port * 10 + digit - '0';
            }
            return port <= 65535;
        }
    }
}
