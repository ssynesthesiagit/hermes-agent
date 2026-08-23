package com.hermes.gatewayclient;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class SpeechPageScriptTest {

    @Test
    public void safelyQuotesDictationForJavascript() {
        assertEquals(
            "\"say \\\"hello\\\"\\nthen \\\\ stop\\u2028\"",
            SpeechPageScript.quote("say \"hello\"\nthen \\ stop\u2028")
        );
    }

    @Test
    public void scriptsAreScopedToHermesChatInsteadOfGenericInputs() {
        String composer = SpeechPageScript.hasComposer();
        String insert = SpeechPageScript.insertTranscript("hello");
        String latest = SpeechPageScript.latestReply();

        assertTrue(composer.contains("mobile-console__chat-panel"));
        assertTrue(insert.contains("mobile-console__composer"));
        assertTrue(latest.contains("mobile-message--assistant"));
        assertFalse(insert.contains("input[type"));
        assertFalse(insert.contains("contenteditable"));
    }

    @Test
    public void latestReplyReadsTheWholeRenderedMessageBody() {
        String latest = SpeechPageScript.latestReply();

        assertTrue(latest.contains("row.children"));
        assertTrue(latest.contains("mobile-message__meta"));
        assertTrue(latest.contains("mobile-message__actions"));
        assertTrue(latest.contains("body.innerText"));
        assertFalse(latest.contains("querySelector('p')"));
    }
}
