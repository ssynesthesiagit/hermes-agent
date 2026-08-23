package com.hermes.gatewayclient;

import android.Manifest;
import android.app.AlertDialog;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.text.InputFilter;
import android.text.InputType;
import android.webkit.CookieManager;
import android.webkit.ServiceWorkerClient;
import android.webkit.ServiceWorkerController;
import android.webkit.ServiceWorkerWebSettings;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Toast;
import androidx.activity.OnBackPressedCallback;
import androidx.core.content.ContextCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import com.getcapacitor.BridgeActivity;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class MainActivity extends BridgeActivity {

    private static final int MICROPHONE_PERMISSION_REQUEST = 4107;

    private SecureGatewayStore gatewayStore;
    private HermesSpeechController speechController;
    private SpeechControlRail speechControls;
    private String savedGatewayUrl;
    private AlertDialog gatewayDialog;
    private boolean gatewaySecurityReady;
    private boolean gatewayLoadFailed;
    private boolean unsupportedWebView;
    private final OnBackPressedCallback backPressedCallback = new OnBackPressedCallback(true) {
        @Override
        public void handleOnBackPressed() {
            handleBackPressed();
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (getBridge() == null) {
            return;
        }

        gatewayStore = new SecureGatewayStore(this);
        getOnBackPressedDispatcher().addCallback(this, backPressedCallback);
        WebView webView = getBridge().getWebView();
        WebSettings settings = webView.getSettings();
        settings.setDomStorageEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        webView.clearCache(true);
        speechControls = new SpeechControlRail(this, webView, new SpeechControlRail.Actions() {
            @Override
            public void onDictation() {
                boolean permissionGranted = ContextCompat.checkSelfPermission(
                    MainActivity.this,
                    Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED;
                speechController.toggleDictation(permissionGranted, () -> requestPermissions(
                    new String[] { Manifest.permission.RECORD_AUDIO },
                    MICROPHONE_PERMISSION_REQUEST
                ));
            }

            @Override
            public void onReadReply() {
                speechController.readLatestReply();
            }

            @Override
            public void onToggleAutoRead() {
                speechController.toggleAutoRead();
            }
        });
        speechController = new HermesSpeechController(this, webView, new HermesSpeechController.UiListener() {
            @Override
            public void showStatus(String message) {
                Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show();
            }

            @Override
            public void onListeningChanged(boolean listening) {
                speechControls.setListening(listening);
            }

            @Override
            public void onSpeakingChanged(boolean speaking) {
                speechControls.setSpeaking(speaking);
            }

            @Override
            public void onAutoReadChanged(boolean enabled) {
                speechControls.setAutoRead(enabled);
            }
        });
        getBridge().setWebViewClient(new GatewayWebViewClient(getBridge(), new GatewayWebViewClient.MainFrameLoadListener() {
            @Override
            public void onMainFrameLoadFailed() {
                gatewayLoadFailed = true;
                speechControls.setVisible(false);
            }

            @Override
            public void onMainFrameLoaded() {
                gatewayLoadFailed = false;
                speechControls.setVisible(GatewayMobileUrl.isMobileRoute(webView.getUrl()));
                speechController.onPageLoaded();
            }
        }));

        if (!installGatewaySecurity(webView)) {
            unsupportedWebView = true;
            webView.post(this::showUnsupportedWebViewError);
            return;
        }
        gatewaySecurityReady = true;

        SecureGatewayStore.GatewayState storedState = gatewayStore.getState();
        SecureGatewayStore.Gateway activeGateway = storedState.getActiveGateway();
        if (activeGateway != null && loadApprovedGateway(activeGateway.getUrl())) {
            return;
        }

        // An empty or unusable configuration never opens a blank shell. The
        // picker will force the first add because there is no current URL to
        // return to.
        webView.post(this::showGatewayPicker);
    }

    @Override
    public void onPause() {
        if (speechController != null) {
            speechController.onPause();
        }
        WebView webView = getBridge() == null ? null : getBridge().getWebView();
        if (webView != null) {
            webView.onPause();
        }
        // CookieManager and DOM storage are intentionally not cleared. WebView
        // keeps them partitioned by gateway origin when the active URL changes.
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override
    public void onResume() {
        super.onResume();
        if (speechController != null) {
            speechController.onResume();
        }
        WebView webView = getBridge() == null ? null : getBridge().getWebView();
        if (webView != null) {
            webView.onResume();
            if (unsupportedWebView) {
                return;
            }
            if (gatewayLoadFailed && gatewaySecurityReady && savedGatewayUrl != null) {
                gatewayLoadFailed = false;
                loadApprovedGateway(savedGatewayUrl);
                return;
            }
            webView.post(() -> webView.evaluateJavascript(
                "window.dispatchEvent(new Event('online'));document.dispatchEvent(new Event('visibilitychange'));",
                null
            ));
        }
    }

    @Override
    public void onDestroy() {
        if (speechController != null) {
            speechController.destroy();
        }
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(
        int requestCode,
        String[] permissions,
        int[] grantResults
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != MICROPHONE_PERMISSION_REQUEST || speechController == null) {
            return;
        }
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            speechController.startDictationAfterPermission();
        } else {
            Toast.makeText(
                this,
                "Microphone permission is required for bot-message dictation.",
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private void handleBackPressed() {
        if (gatewayDialog != null && gatewayDialog.isShowing()) {
            return;
        }

        WebView webView = getBridge() == null ? null : getBridge().getWebView();
        if (webView != null && webView.canGoBack()) {
            // Keep ordinary hosted-page history behavior. The gateway picker is
            // only a root-level Android navigation affordance.
            webView.goBack();
            return;
        }
        showGatewayPicker();
    }

    private void showGatewayPicker() {
        if (isFinishing() || (gatewayDialog != null && gatewayDialog.isShowing())) {
            return;
        }

        SecureGatewayStore.GatewayState state = gatewayStore.getState();
        List<SecureGatewayStore.Gateway> gateways = approvedGateways(state);
        String[] entries = new String[gateways.size()];
        int checkedItem = -1;
        for (int index = 0; index < gateways.size(); index++) {
            SecureGatewayStore.Gateway gateway = gateways.get(index);
            boolean current = gateway.getUrl().equals(state.getActiveUrl());
            entries[index] = (current ? "✓ " : "") + gateway.getName() + "\n" + gateway.getUrl();
            if (current) {
                checkedItem = index;
            }
        }

        SecureGatewayStore.Gateway currentGateway = state.getActiveGateway();
        if (currentGateway != null && !TailscaleUrlPolicy.isAllowed(currentGateway.getUrl())) {
            currentGateway = null;
        }
        final SecureGatewayStore.Gateway pickerCurrentGateway = currentGateway;

        AlertDialog.Builder builder = new AlertDialog.Builder(this)
            .setTitle("Hermes gateways")
            .setPositiveButton("+ Add gateway", null);
        if (entries.length == 0) {
            builder.setMessage("Add a Tailscale-connected Hermes gateway to continue.");
        } else {
            builder.setSingleChoiceItems(entries, checkedItem, (dialog, which) -> {
                SecureGatewayStore.Gateway selected = gateways.get(which);
                if (selectAndLoadGateway(selected.getUrl())) {
                    dialog.dismiss();
                }
            });
        }

        if (pickerCurrentGateway != null) {
            builder.setNeutralButton("Edit current", null);
        }

        boolean canCancel = savedGatewayUrl != null;
        if (canCancel) {
            builder.setNegativeButton("Cancel", null);
        }

        AlertDialog dialog = builder.create();
        gatewayDialog = dialog;
        dialog.setCancelable(canCancel);
        dialog.setOnShowListener(ignored -> {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
                dismissGatewayDialog(dialog);
                showGatewayForm(null, canCancel);
            });
            if (pickerCurrentGateway != null) {
                dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(view -> {
                    dismissGatewayDialog(dialog);
                    showGatewayForm(pickerCurrentGateway, true);
                });
            }
        });
        dialog.setOnDismissListener(ignored -> {
            if (gatewayDialog == dialog) {
                gatewayDialog = null;
            }
        });
        dialog.show();
    }

    private void showGatewayForm(SecureGatewayStore.Gateway currentGateway, boolean canCancel) {
        if (isFinishing() || (gatewayDialog != null && gatewayDialog.isShowing())) {
            return;
        }

        EditText nameInput = new EditText(this);
        nameInput.setSingleLine(true);
        nameInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        nameInput.setHint("Name (optional; defaults to host)");
        nameInput.setFilters(new InputFilter[] {
            new InputFilter.LengthFilter(SecureGatewayStore.MAX_LABEL_LENGTH)
        });
        if (currentGateway != null) {
            nameInput.setText(currentGateway.getName());
            nameInput.setSelection(nameInput.length());
        }

        EditText urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        urlInput.setHint("http://100.64.x.x:9119 or https://name.ts.net");
        urlInput.setFilters(new InputFilter[] {
            new InputFilter.LengthFilter(SecureGatewayStore.MAX_URL_LENGTH)
        });
        if (currentGateway != null) {
            urlInput.setText(currentGateway.getUrl());
            urlInput.setSelection(urlInput.length());
        }

        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        int horizontalPadding = (int) (24 * getResources().getDisplayMetrics().density);
        container.setPadding(horizontalPadding, 0, horizontalPadding, 0);
        container.addView(nameInput, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ));
        container.addView(urlInput, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle(currentGateway == null ? "Add gateway" : "Edit current gateway")
            .setMessage("Use only an HTTP(S) Tailscale address (.ts.net or 100.64.0.0/10 / fd7a:115c:a1e0::/48).")
            .setView(container)
            .setPositiveButton("Save", null)
            .create();
        if (canCancel) {
            dialog.setButton(AlertDialog.BUTTON_NEGATIVE, "Cancel", (ignored, which) -> dialog.dismiss());
        }

        gatewayDialog = dialog;
        dialog.setCancelable(canCancel);
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String candidate = urlInput.getText().toString().trim();
            String approvedUrl = TailscaleUrlPolicy.validateAndNormalize(candidate);
            if (approvedUrl == null) {
                urlInput.setError("Use only a valid HTTP(S) Tailscale gateway URL");
                return;
            }

            String name = nameInput.getText().toString().trim();
            try {
                if (currentGateway == null) {
                    gatewayStore.addOrUpdateGateway(name, approvedUrl);
                } else {
                    gatewayStore.updateCurrentGateway(name, approvedUrl);
                }
            } catch (SecureGatewayStore.SecureStoreException ex) {
                urlInput.setError("The gateway could not be stored securely");
                return;
            }

            String activeUrl = gatewayStore.getState().getActiveUrl();
            if (!loadApprovedGateway(activeUrl)) {
                urlInput.setError("The saved gateway URL is not allowed");
                return;
            }
            dialog.dismiss();
        }));
        dialog.setOnDismissListener(ignored -> {
            if (gatewayDialog == dialog) {
                gatewayDialog = null;
            }
        });
        dialog.show();
    }

    private boolean selectAndLoadGateway(String url) {
        String approvedUrl = TailscaleUrlPolicy.validateAndNormalize(url);
        if (approvedUrl == null) {
            return false;
        }
        try {
            gatewayStore.selectGateway(approvedUrl);
        } catch (SecureGatewayStore.SecureStoreException ex) {
            return false;
        }
        return loadApprovedGateway(approvedUrl);
    }

    private boolean loadApprovedGateway(String url) {
        String approvedUrl = TailscaleUrlPolicy.validateAndNormalize(url);
        if (approvedUrl == null || getBridge() == null) {
            return false;
        }

        savedGatewayUrl = approvedUrl;
        gatewayLoadFailed = false;
        WebView webView = getBridge().getWebView();
        if (speechController != null) {
            speechController.prepareForGatewayNavigation();
        }
        if (speechControls != null) {
            speechControls.setVisible(false);
        }
        webView.loadUrl(GatewayMobileUrl.toMobileUrl(approvedUrl, BuildConfig.VERSION_CODE));
        return true;
    }

    private static List<SecureGatewayStore.Gateway> approvedGateways(SecureGatewayStore.GatewayState state) {
        List<SecureGatewayStore.Gateway> approved = new ArrayList<>();
        for (SecureGatewayStore.Gateway gateway : state.getGateways()) {
            if (TailscaleUrlPolicy.isAllowed(gateway.getUrl())) {
                approved.add(gateway);
            }
        }
        return approved;
    }

    private void dismissGatewayDialog(AlertDialog dialog) {
        dialog.dismiss();
        if (gatewayDialog == dialog) {
            gatewayDialog = null;
        }
    }

    private boolean installGatewaySecurity(WebView webView) {
        if (!TailscaleEgressGuard.canLoadGateway(
            WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT))) {
            return false;
        }

        try {
            // The wildcard rule is intentional: the guard must run in every frame before
            // any remote-origin page script, including a frame whose origin changes later.
            WebViewCompat.addDocumentStartJavaScript(
                webView,
                TailscaleEgressGuard.script(),
                Collections.singleton("*")
            );
            installServiceWorkerGuard();
            return true;
        } catch (RuntimeException ignored) {
            // A missing/partially supported document-start implementation is fail-closed.
            return false;
        }
    }

    private void installServiceWorkerGuard() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            return;
        }

        ServiceWorkerController controller = ServiceWorkerController.getInstance();
        ServiceWorkerWebSettings settings = controller.getServiceWorkerWebSettings();
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccess(false);
        settings.setBlockNetworkLoads(true);
        controller.setServiceWorkerClient(new ServiceWorkerClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebResourceRequest request) {
                if (request != null && request.getUrl() != null
                    && TailscaleUrlPolicy.isAllowed(request.getUrl().toString())) {
                    return null;
                }
                return GatewayWebViewClient.blockedResponse();
            }
        });
    }

    private void showUnsupportedWebViewError() {
        if (isFinishing()) {
            return;
        }
        new AlertDialog.Builder(this)
            .setTitle("Unsupported Android WebView")
            .setMessage("Hermes requires an Android System WebView with document-start script support. Update Android System WebView, then reopen Hermes.")
            .setPositiveButton("OK", null)
            .setCancelable(false)
            .show();
    }
}
