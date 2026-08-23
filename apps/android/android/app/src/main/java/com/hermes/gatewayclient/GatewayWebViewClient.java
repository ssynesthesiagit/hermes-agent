package com.hermes.gatewayclient;

import android.graphics.Bitmap;
import android.net.Uri;
import android.net.http.SslError;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeWebViewClient;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;

/** Keeps Capacitor's bridge behavior while constraining network navigation to Tailscale. */
final class GatewayWebViewClient extends BridgeWebViewClient {

    interface MainFrameLoadListener {
        void onMainFrameLoadFailed();

        void onMainFrameLoaded();
    }

    private final Uri localOrigin;
    private final MainFrameLoadListener mainFrameLoadListener;
    private boolean mainFrameNavigationFailed;

    GatewayWebViewClient(Bridge bridge) {
        this(bridge, null);
    }

    GatewayWebViewClient(Bridge bridge, MainFrameLoadListener mainFrameLoadListener) {
        super(bridge);
        this.localOrigin = Uri.parse(bridge.getLocalUrl());
        this.mainFrameLoadListener = mainFrameLoadListener;
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        return !isAllowedMainFrameUrl(request.getUrl());
    }

    @SuppressWarnings("deprecation")
    @Override
    public boolean shouldOverrideUrlLoading(WebView view, String url) {
        return !isAllowedMainFrameUrl(Uri.parse(url));
    }

    @Override
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
        Uri url = request.getUrl();
        if (isLocalUrl(url) || TailscaleUrlPolicy.isAllowed(url.toString())) {
            return super.shouldInterceptRequest(view, request);
        }
        return blockedResponse();
    }

    @Override
    public void onPageFinished(WebView view, String url) {
        super.onPageFinished(view, url);
        if (!mainFrameNavigationFailed && mainFrameLoadListener != null && TailscaleUrlPolicy.isAllowed(url)) {
            mainFrameLoadListener.onMainFrameLoaded();
        }
    }

    @Override
    public void onPageStarted(WebView view, String url, Bitmap favicon) {
        mainFrameNavigationFailed = false;
        super.onPageStarted(view, url, favicon);
    }

    @Override
    public void onReceivedError(WebView view, WebResourceRequest request, android.webkit.WebResourceError error) {
        if (request.isForMainFrame()) {
            markMainFrameNavigationFailed();
        }
        super.onReceivedError(view, request, error);
    }

    @Override
    public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
        if (request.isForMainFrame()) {
            markMainFrameNavigationFailed();
        }
        super.onReceivedHttpError(view, request, errorResponse);
    }

    @Override
    public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
        // Never permit a certificate exception, including for a Tailscale host.
        // WebView exposes no isForMainFrame flag for TLS errors; cancellation is therefore
        // treated as a failed navigation so a later resume can retry the saved gateway.
        markMainFrameNavigationFailed();
        handler.cancel();
    }

    private void markMainFrameNavigationFailed() {
        mainFrameNavigationFailed = true;
        if (mainFrameLoadListener != null) {
            mainFrameLoadListener.onMainFrameLoadFailed();
        }
    }

    private boolean isAllowedMainFrameUrl(Uri url) {
        return isLocalUrl(url) || TailscaleUrlPolicy.isAllowed(url.toString());
    }

    private boolean isLocalUrl(Uri url) {
        return url != null
            && localOrigin != null
            && equalsIgnoreCase(localOrigin.getScheme(), url.getScheme())
            && equalsIgnoreCase(localOrigin.getHost(), url.getHost())
            && localOrigin.getPort() == url.getPort();
    }

    private static boolean equalsIgnoreCase(String left, String right) {
        return left == null ? right == null : left.equalsIgnoreCase(right);
    }

    static WebResourceResponse blockedResponse() {
        return new WebResourceResponse(
            "text/plain",
            "UTF-8",
            403,
            "Forbidden",
            Collections.emptyMap(),
            new ByteArrayInputStream("Blocked by Hermes Tailscale URL policy".getBytes(StandardCharsets.UTF_8))
        );
    }
}
