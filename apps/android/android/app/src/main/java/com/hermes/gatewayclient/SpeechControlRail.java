package com.hermes.gatewayclient;

import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.Drawable;
import android.os.Build;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.appcompat.content.res.AppCompatResources;
import androidx.coordinatorlayout.widget.CoordinatorLayout;
import androidx.core.graphics.drawable.DrawableCompat;

/** Compact native controls that remain available above the hosted bot UI. */
final class SpeechControlRail {

    interface Actions {
        void onDictation();

        void onReadReply();

        void onToggleAutoRead();
    }

    private static final int IDLE_COLOR = Color.rgb(90, 238, 198);
    private static final int ACTIVE_COLOR = Color.rgb(255, 91, 193);

    private final Context context;
    private final LinearLayout rail;
    private final TextView micButton;
    private final TextView readButton;
    private final TextView autoButton;

    SpeechControlRail(Context context, WebView webView, Actions actions) {
        this.context = context;
        ViewGroup parent = (ViewGroup) webView.getParent();

        rail = new LinearLayout(context);
        rail.setOrientation(LinearLayout.VERTICAL);
        rail.setGravity(Gravity.CENTER);
        rail.setPadding(dp(3), dp(4), dp(3), dp(4));
        rail.setBackgroundResource(R.drawable.speech_rail_background);
        rail.setElevation(dp(10));
        rail.setContentDescription("Hermes speech controls");
        rail.setVisibility(View.GONE);

        micButton = createButton(R.drawable.ic_speech_mic, "MIC", "Dictate a bot message");
        readButton = createButton(R.drawable.ic_speech_volume, "READ", "Read the latest bot reply");
        autoButton = createButton(R.drawable.ic_speech_auto, "AUTO", "Automatically read new bot replies");
        micButton.setOnClickListener(ignored -> actions.onDictation());
        readButton.setOnClickListener(ignored -> actions.onReadReply());
        autoButton.setOnClickListener(ignored -> actions.onToggleAutoRead());

        rail.addView(micButton);
        rail.addView(readButton);
        rail.addView(autoButton);

        CoordinatorLayout.LayoutParams parameters = new CoordinatorLayout.LayoutParams(
            dp(58),
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        parameters.gravity = Gravity.END | Gravity.CENTER_VERTICAL;
        parameters.setMarginEnd(dp(7));
        parent.addView(rail, parameters);
        rail.bringToFront();
    }

    void setVisible(boolean visible) {
        rail.setVisibility(visible ? View.VISIBLE : View.GONE);
    }

    void setListening(boolean listening) {
        updateSelected(micButton, R.drawable.ic_speech_mic, listening);
        micButton.setContentDescription(listening ? "Stop dictation" : "Dictate a bot message");
    }

    void setSpeaking(boolean speaking) {
        updateSelected(readButton, R.drawable.ic_speech_volume, speaking);
        readButton.setContentDescription(speaking ? "Stop reading the bot reply" : "Read the latest bot reply");
    }

    void setAutoRead(boolean enabled) {
        updateSelected(autoButton, R.drawable.ic_speech_auto, enabled);
        autoButton.setContentDescription(enabled
            ? "Turn off automatic bot-reply reading"
            : "Automatically read new bot replies");
    }

    private TextView createButton(int iconResource, String label, String description) {
        TextView button = new TextView(context);
        LinearLayout.LayoutParams parameters = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            dp(57)
        );
        button.setLayoutParams(parameters);
        button.setGravity(Gravity.CENTER);
        button.setText(label);
        button.setTextSize(9);
        button.setAllCaps(true);
        button.setLetterSpacing(0.09f);
        button.setCompoundDrawablePadding(dp(2));
        button.setPadding(dp(2), dp(5), dp(2), dp(4));
        button.setBackgroundResource(R.drawable.speech_button_background);
        button.setContentDescription(description);
        button.setClickable(true);
        button.setFocusable(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            button.setTooltipText(description);
        }
        updateSelected(button, iconResource, false);
        return button;
    }

    private void updateSelected(TextView button, int iconResource, boolean selected) {
        int color = selected ? ACTIVE_COLOR : IDLE_COLOR;
        Drawable icon = AppCompatResources.getDrawable(context, iconResource);
        if (icon != null) {
            icon = DrawableCompat.wrap(icon.mutate());
            DrawableCompat.setTint(icon, color);
        }
        button.setCompoundDrawablesRelativeWithIntrinsicBounds(null, icon, null, null);
        button.setTextColor(color);
        button.setSelected(selected);
    }

    private int dp(int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }
}
