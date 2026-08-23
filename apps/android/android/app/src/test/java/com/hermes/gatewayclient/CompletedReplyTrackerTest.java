package com.hermes.gatewayclient;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

public class CompletedReplyTrackerTest {

    @Test
    public void skipsVisibleBaselineAndSpeaksNewCompletedReplyOnce() {
        CompletedReplyTracker tracker = new CompletedReplyTracker(700);
        tracker.prime(reply("bot-a", "old", "Already here", false));

        assertNull(tracker.observe(reply("bot-a", "old", "Already here", false), 1000));
        assertNull(tracker.observe(reply("bot-a", "new", "Fresh reply", false), 1100));
        assertNull(tracker.observe(reply("bot-a", "new", "Fresh reply", false), 1799));
        assertEquals("Fresh reply", tracker.observe(reply("bot-a", "new", "Fresh reply", false), 1800));
        assertNull(tracker.observe(reply("bot-a", "new", "Fresh reply", false), 3000));
    }

    @Test
    public void waitsForStreamingToFinishAndRemainStable() {
        CompletedReplyTracker tracker = new CompletedReplyTracker(500);
        tracker.prime(new CompletedReplyTracker.Snapshot("bot-a", "", "", false));

        assertNull(tracker.observe(reply("bot-a", "stream-1", "Hel", true), 100));
        assertNull(tracker.observe(reply("bot-a", "stream-2", "Hello", true), 300));
        assertNull(tracker.observe(reply("bot-a", "final", "Hello", false), 500));
        assertEquals("Hello", tracker.observe(reply("bot-a", "final", "Hello", false), 1000));
    }

    @Test
    public void changingBotsPrimesInsteadOfReadingOldHistory() {
        CompletedReplyTracker tracker = new CompletedReplyTracker(0);
        tracker.prime(reply("bot-a", "a-1", "A history", false));

        assertNull(tracker.observe(reply("bot-b", "b-1", "B history", false), 100));
        assertNull(tracker.observe(reply("bot-b", "b-1", "B history", false), 101));
        assertNull(tracker.observe(reply("bot-b", "b-2", "B new", false), 200));
        assertEquals("B new", tracker.observe(reply("bot-b", "b-2", "B new", false), 201));
    }

    private static CompletedReplyTracker.Snapshot reply(
        String context,
        String id,
        String text,
        boolean streaming
    ) {
        return new CompletedReplyTracker.Snapshot(context, id, text, streaming);
    }
}
