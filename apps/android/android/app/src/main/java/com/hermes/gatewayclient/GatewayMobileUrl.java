package com.hermes.gatewayclient;

import java.net.URI;
import java.net.URISyntaxException;

/** Builds the hosted mobile route without changing the stored gateway URL. */
final class GatewayMobileUrl {

    private static final String MOBILE_PATH = "/mobile";
    private static final String CLIENT_QUERY_KEY = "hermes_client";

    private GatewayMobileUrl() {}

    static String toMobileUrl(String baseUrl, int clientRevision) {
        if (clientRevision < 1) {
            throw new IllegalArgumentException("Client revision must be positive");
        }
        if (baseUrl == null) {
            throw new IllegalArgumentException("Gateway URL is null");
        }

        final URI uri;
        try {
            uri = new URI(baseUrl);
        } catch (URISyntaxException ex) {
            throw new IllegalArgumentException("Gateway URL is malformed", ex);
        }

        if (uri.getScheme() == null || uri.getRawAuthority() == null) {
            throw new IllegalArgumentException("Gateway URL must have a scheme and authority");
        }

        String path = uri.getRawPath();
        if (path == null) {
            path = "";
        }
        while (path.endsWith("/")) {
            path = path.substring(0, path.length() - 1);
        }

        if (path.isEmpty()) {
            path = MOBILE_PATH;
        } else if (!path.endsWith(MOBILE_PATH)) {
            path += MOBILE_PATH;
        }

        StringBuilder mobileUrl = new StringBuilder(baseUrl.length() + MOBILE_PATH.length() + 32);
        mobileUrl.append(uri.getScheme()).append("://").append(uri.getRawAuthority()).append(path);
        String query = withoutClientQueryItems(uri.getRawQuery());
        mobileUrl.append('?');
        if (!query.isEmpty()) {
            mobileUrl.append(query).append('&');
        }
        mobileUrl.append("hermes_client=android-").append(clientRevision);
        if (uri.getRawFragment() != null) {
            mobileUrl.append('#').append(uri.getRawFragment());
        }
        return mobileUrl.toString();
    }

    static boolean isMobileRoute(String candidate) {
        if (candidate == null) {
            return false;
        }
        try {
            String path = new URI(candidate).getPath();
            if (path == null) {
                return false;
            }
            while (path.endsWith("/") && path.length() > 1) {
                path = path.substring(0, path.length() - 1);
            }
            return path.equals(MOBILE_PATH) || path.endsWith(MOBILE_PATH);
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    private static String withoutClientQueryItems(String rawQuery) {
        if (rawQuery == null || rawQuery.isEmpty()) {
            return "";
        }

        StringBuilder query = new StringBuilder(rawQuery.length());
        boolean hasQueryItem = false;
        int itemStart = 0;
        while (itemStart <= rawQuery.length()) {
            int itemEnd = rawQuery.indexOf('&', itemStart);
            if (itemEnd == -1) {
                itemEnd = rawQuery.length();
            }

            String item = rawQuery.substring(itemStart, itemEnd);
            if (!item.isEmpty() && !isClientQueryItem(item)) {
                if (hasQueryItem) {
                    query.append('&');
                }
                query.append(item);
                hasQueryItem = true;
            }

            if (itemEnd == rawQuery.length()) {
                break;
            }
            itemStart = itemEnd + 1;
        }
        return query.toString();
    }

    private static boolean isClientQueryItem(String item) {
        return item.equals(CLIENT_QUERY_KEY) || item.startsWith(CLIENT_QUERY_KEY + "=");
    }
}
