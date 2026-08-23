package com.hermes.gatewayclient;

import android.content.Intent;
import android.media.AudioAttributes;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import android.webkit.WebView;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import org.json.JSONArray;
import org.json.JSONException;

/** Native Android speech around the hosted Hermes bot-chat page. */
final class HermesSpeechController {

    interface UiListener {
        void showStatus(String message);

        void onListeningChanged(boolean listening);

        void onSpeakingChanged(boolean speaking);

        void onAutoReadChanged(boolean enabled);
    }

    private interface SnapshotCallback {
        void onSnapshot(CompletedReplyTracker.Snapshot snapshot);
    }

    private static final String TAG = "HermesSpeech";
    private static final long POLL_INTERVAL_MILLIS = 850;
    private static final long COMPLETION_QUIET_MILLIS = 700;

    private final MainActivity activity;
    private final WebView webView;
    private final UiListener ui;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final CompletedReplyTracker replyTracker =
        new CompletedReplyTracker(COMPLETION_QUIET_MILLIS);
    private final Runnable pollRunnable = this::pollLatestReply;

    private SpeechRecognizer speechRecognizer;
    private TextToSpeech textToSpeech;
    private boolean listening;
    private boolean ignoreNextRecognitionError;
    private boolean autoReadEnabled;
    private boolean baselinePending;
    private boolean manualReadPending;
    private boolean pollScheduled;
    private boolean pollInFlight;
    private boolean foreground = true;
    private boolean destroyed;
    private boolean ttsInitializationComplete;
    private boolean ttsAvailable;
    private boolean speaking;
    private String pendingSpeechText;
    private final List<String> speechChunks = new ArrayList<>();
    private int nextSpeechChunkIndex;
    private String activeUtteranceId;
    private int activeSpeechSequence;
    private int utteranceSequence;

    HermesSpeechController(MainActivity activity, WebView webView, UiListener ui) {
        this.activity = activity;
        this.webView = webView;
        this.ui = ui;
        initializeTextToSpeech();
    }

    void toggleDictation(boolean microphonePermissionGranted, Runnable requestPermission) {
        if (listening) {
            speechRecognizer.stopListening();
            ui.showStatus("Finishing dictation…");
            return;
        }
        if (!isHostedHermesPage()) {
            ui.showStatus("Connect to a Hermes gateway first.");
            return;
        }

        webView.evaluateJavascript(SpeechPageScript.hasComposer(), result -> {
            if (!"true".equals(result)) {
                ui.showStatus("Open Chat and choose a bot before dictating.");
                return;
            }
            if (!microphonePermissionGranted) {
                requestPermission.run();
                return;
            }
            startDictationAfterPermission();
        });
    }

    void startDictationAfterPermission() {
        if (destroyed || listening || !isHostedHermesPage()) {
            return;
        }
        webView.evaluateJavascript(SpeechPageScript.hasComposer(), result -> {
            if ("true".equals(result)) {
                startRecognizer();
            } else {
                ui.showStatus("Open Chat and choose a bot before dictating.");
            }
        });
    }

    void readLatestReply() {
        if (speaking || pendingSpeechText != null || manualReadPending) {
            stopSpeaking();
            manualReadPending = false;
            ui.showStatus("Playback stopped.");
            return;
        }
        if (!isHostedHermesPage()) {
            ui.showStatus("Connect to a Hermes gateway first.");
            return;
        }

        queryLatestReply(snapshot -> {
            if (snapshot == null || (!snapshot.hasReply() && !snapshot.streaming)) {
                ui.showStatus("No bot reply is visible in Chat yet.");
                return;
            }
            if (snapshot.streaming) {
                manualReadPending = true;
                ui.showStatus("I’ll read this reply when the bot finishes.");
                schedulePoll();
                return;
            }
            replyTracker.markConsumed(snapshot);
            speak(snapshot.text);
        });
    }

    void toggleAutoRead() {
        autoReadEnabled = !autoReadEnabled;
        baselinePending = autoReadEnabled;
        webView.setKeepScreenOn(autoReadEnabled);
        ui.onAutoReadChanged(autoReadEnabled);
        if (autoReadEnabled) {
            ui.showStatus("Auto-read on. New completed bot replies will play; the screen stays awake.");
            schedulePoll();
        } else {
            baselinePending = false;
            replyTracker.prime(null);
            ui.showStatus("Auto-read off.");
            stopPollingIfIdle();
        }
    }

    void prepareForGatewayNavigation() {
        manualReadPending = false;
        replyTracker.prime(null);
        baselinePending = autoReadEnabled;
    }

    void onPageLoaded() {
        if (autoReadEnabled) {
            baselinePending = true;
            schedulePoll();
        }
    }

    void onPause() {
        foreground = false;
        cancelDictation(true);
        handler.removeCallbacks(pollRunnable);
        pollScheduled = false;
    }

    void onResume() {
        foreground = true;
        if (autoReadEnabled || manualReadPending) {
            schedulePoll();
        }
    }

    void destroy() {
        destroyed = true;
        foreground = false;
        handler.removeCallbacksAndMessages(null);
        cancelDictation(true);
        if (speechRecognizer != null) {
            speechRecognizer.destroy();
            speechRecognizer = null;
        }
        if (textToSpeech != null) {
            textToSpeech.stop();
            textToSpeech.shutdown();
            textToSpeech = null;
        }
        webView.setKeepScreenOn(false);
    }

    private void initializeTextToSpeech() {
        TextToSpeech engine = new TextToSpeech(activity.getApplicationContext(), status ->
            handler.post(() -> finishTextToSpeechInitialization(status))
        );
        textToSpeech = engine;
    }

    private void finishTextToSpeechInitialization(int status) {
        if (destroyed || textToSpeech == null) {
            return;
        }
        ttsInitializationComplete = true;
        if (status != TextToSpeech.SUCCESS) {
            Log.w(TAG, "Android text-to-speech initialization failed: " + status);
            ttsAvailable = false;
            return;
        }

        int languageResult = textToSpeech.setLanguage(Locale.getDefault());
        if (languageResult == TextToSpeech.LANG_MISSING_DATA
            || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
            languageResult = textToSpeech.setLanguage(Locale.US);
        }
        ttsAvailable = languageResult != TextToSpeech.LANG_MISSING_DATA
            && languageResult != TextToSpeech.LANG_NOT_SUPPORTED;
        textToSpeech.setSpeechRate(1.0f);
        textToSpeech.setAudioAttributes(new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build());
        textToSpeech.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override
            public void onStart(String utteranceId) {
                handler.post(() -> {
                    if (utteranceId != null && utteranceId.equals(activeUtteranceId)) {
                        setSpeaking(true);
                    }
                });
            }

            @Override
            public void onDone(String utteranceId) {
                handler.post(() -> continueAfterUtterance(utteranceId));
            }

            @Override
            public void onError(String utteranceId) {
                onUtteranceError(utteranceId);
            }

            @Override
            public void onError(String utteranceId, int errorCode) {
                onUtteranceError(utteranceId);
            }
        });
        Log.i(TAG, "Android text-to-speech ready");

        if (ttsAvailable && pendingSpeechText != null) {
            String pending = pendingSpeechText;
            pendingSpeechText = null;
            speak(pending);
        }
    }

    private void onUtteranceError(String utteranceId) {
        handler.post(() -> {
            if (utteranceId != null && utteranceId.equals(activeUtteranceId)) {
                clearSpeechQueue();
                setSpeaking(false);
                ui.showStatus("Android could not play that bot reply.");
            }
        });
    }

    private void startRecognizer() {
        stopSpeaking();
        if (!SpeechRecognizer.isRecognitionAvailable(activity)) {
            ui.showStatus("No Android speech-recognition service is installed.");
            return;
        }
        if (speechRecognizer == null) {
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(activity);
            speechRecognizer.setRecognitionListener(new HermesRecognitionListener());
        }

        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag());
        ignoreNextRecognitionError = false;
        listening = true;
        ui.onListeningChanged(true);
        ui.showStatus("Listening… tap MIC again when you’re done.");
        try {
            speechRecognizer.startListening(intent);
        } catch (RuntimeException exception) {
            listening = false;
            ui.onListeningChanged(false);
            ui.showStatus("Android speech recognition could not start.");
            Log.w(TAG, "Speech recognition start failed", exception);
        }
    }

    private void insertTranscript(String transcript) {
        if (transcript == null || transcript.trim().isEmpty()) {
            ui.showStatus("I didn’t catch any words.");
            return;
        }
        webView.evaluateJavascript(SpeechPageScript.insertTranscript(transcript), result -> {
            if ("\"inserted\"".equals(result)) {
                ui.showStatus("Dictation added. Review it, then tap Send.");
            } else {
                ui.showStatus("The Chat composer closed before dictation finished.");
            }
        });
    }

    private void cancelDictation(boolean silent) {
        if (speechRecognizer == null || !listening) {
            return;
        }
        ignoreNextRecognitionError = silent;
        speechRecognizer.cancel();
        listening = false;
        ui.onListeningChanged(false);
    }

    private void speak(String text) {
        if (text == null || text.trim().isEmpty()) {
            ui.showStatus("That bot reply has no readable text.");
            return;
        }
        if (!ttsInitializationComplete) {
            pendingSpeechText = text;
            ui.showStatus("Preparing Android’s voice…");
            return;
        }
        if (!ttsAvailable || textToSpeech == null) {
            ui.showStatus("Install or enable Android text-to-speech, then try again.");
            return;
        }

        int limit = Math.min(3500, TextToSpeech.getMaxSpeechInputLength() - 32);
        List<String> chunks = SpeechChunker.split(text, Math.max(32, limit));
        if (chunks.isEmpty()) {
            return;
        }
        textToSpeech.stop();
        clearSpeechQueue();
        speechChunks.addAll(chunks);
        nextSpeechChunkIndex = 0;
        activeSpeechSequence = ++utteranceSequence;
        setSpeaking(true);
        speakNextChunk();
    }

    private void speakNextChunk() {
        if (textToSpeech == null || nextSpeechChunkIndex >= speechChunks.size()) {
            clearSpeechQueue();
            setSpeaking(false);
            return;
        }

        int chunkIndex = nextSpeechChunkIndex++;
        activeUtteranceId = "hermes-reply-" + activeSpeechSequence + "-" + chunkIndex;
        int result = textToSpeech.speak(
            speechChunks.get(chunkIndex),
            TextToSpeech.QUEUE_FLUSH,
            new Bundle(),
            activeUtteranceId
        );
        if (result == TextToSpeech.ERROR) {
            clearSpeechQueue();
            setSpeaking(false);
            ui.showStatus("Android could not play that bot reply.");
        }
    }

    private void continueAfterUtterance(String utteranceId) {
        if (utteranceId == null || !utteranceId.equals(activeUtteranceId)) {
            return;
        }
        activeUtteranceId = null;
        speakNextChunk();
    }

    private void clearSpeechQueue() {
        speechChunks.clear();
        nextSpeechChunkIndex = 0;
        activeUtteranceId = null;
    }

    private void stopSpeaking() {
        pendingSpeechText = null;
        clearSpeechQueue();
        ++utteranceSequence;
        if (textToSpeech != null) {
            textToSpeech.stop();
        }
        setSpeaking(false);
    }

    private void setSpeaking(boolean nextSpeaking) {
        if (speaking == nextSpeaking) {
            return;
        }
        speaking = nextSpeaking;
        ui.onSpeakingChanged(nextSpeaking);
    }

    private void pollLatestReply() {
        pollScheduled = false;
        if (destroyed || !foreground || (!autoReadEnabled && !manualReadPending) || pollInFlight) {
            return;
        }
        pollInFlight = true;
        queryLatestReply(snapshot -> {
            pollInFlight = false;
            if (destroyed) {
                return;
            }

            if (manualReadPending && snapshot != null && snapshot.hasReply() && !snapshot.streaming) {
                manualReadPending = false;
                baselinePending = false;
                replyTracker.markConsumed(snapshot);
                speak(snapshot.text);
            } else if (autoReadEnabled) {
                if (baselinePending) {
                    if (snapshot != null) {
                        replyTracker.prime(snapshot);
                        baselinePending = false;
                    }
                } else {
                    String completedReply = replyTracker.observe(snapshot, SystemClock.elapsedRealtime());
                    if (completedReply != null) {
                        speak(completedReply);
                    }
                }
            }
            schedulePoll();
        });
    }

    private void queryLatestReply(SnapshotCallback callback) {
        if (!isHostedHermesPage()) {
            callback.onSnapshot(null);
            return;
        }
        webView.evaluateJavascript(SpeechPageScript.latestReply(), result ->
            callback.onSnapshot(parseSnapshot(result))
        );
    }

    private static CompletedReplyTracker.Snapshot parseSnapshot(String value) {
        if (value == null || "null".equals(value)) {
            return null;
        }
        try {
            JSONArray array = new JSONArray(value);
            return new CompletedReplyTracker.Snapshot(
                array.optString(0, ""),
                array.optString(1, ""),
                array.optString(2, ""),
                array.optBoolean(3, false)
            );
        } catch (JSONException exception) {
            Log.w(TAG, "Could not parse Hermes reply snapshot", exception);
            return null;
        }
    }

    private void schedulePoll() {
        if (destroyed || !foreground || pollScheduled || (!autoReadEnabled && !manualReadPending)) {
            return;
        }
        pollScheduled = true;
        handler.postDelayed(pollRunnable, POLL_INTERVAL_MILLIS);
    }

    private void stopPollingIfIdle() {
        if (!manualReadPending) {
            handler.removeCallbacks(pollRunnable);
            pollScheduled = false;
        }
    }

    private boolean isHostedHermesPage() {
        String currentUrl = webView.getUrl();
        return currentUrl != null && TailscaleUrlPolicy.isAllowed(currentUrl);
    }

    private final class HermesRecognitionListener implements RecognitionListener {
        @Override
        public void onReadyForSpeech(Bundle params) {
            Log.i(TAG, "Android speech recognition ready");
        }

        @Override
        public void onBeginningOfSpeech() {}

        @Override
        public void onRmsChanged(float rmsdB) {}

        @Override
        public void onBufferReceived(byte[] buffer) {}

        @Override
        public void onEndOfSpeech() {}

        @Override
        public void onError(int error) {
            listening = false;
            ui.onListeningChanged(false);
            if (ignoreNextRecognitionError) {
                ignoreNextRecognitionError = false;
                return;
            }
            ui.showStatus(recognitionErrorMessage(error));
            Log.w(TAG, "Android speech recognition error: " + error);
        }

        @Override
        public void onResults(Bundle results) {
            listening = false;
            ui.onListeningChanged(false);
            ArrayList<String> matches = results == null
                ? null
                : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
            insertTranscript(matches == null || matches.isEmpty() ? null : matches.get(0));
        }

        @Override
        public void onPartialResults(Bundle partialResults) {}

        @Override
        public void onEvent(int eventType, Bundle params) {}
    }

    private static String recognitionErrorMessage(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_AUDIO:
                return "Android could not use the microphone.";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                return "Microphone permission is required for dictation.";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                return "Speech recognition could not reach its Android service.";
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                return "I didn’t catch any words. Tap MIC and try again.";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                return "Android speech recognition is busy. Try again in a moment.";
            default:
                return "Android speech recognition stopped. Tap MIC to try again.";
        }
    }
}
