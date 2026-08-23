package com.hermes.gatewayclient;

import java.util.List;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class SpeechChunkerTest {

    @Test
    public void leavesShortReplyWhole() {
        assertEquals(List.of("A short bot reply."), SpeechChunker.split("  A short bot reply.  ", 64));
    }

    @Test
    public void splitsLongReplyWithinLimitWithoutDroppingWords() {
        String reply = "First sentence has useful detail. Second sentence keeps going. "
            + "Third sentence finishes the response cleanly.";

        List<String> chunks = SpeechChunker.split(reply, 50);

        assertTrue(chunks.size() > 1);
        for (String chunk : chunks) {
            assertTrue(chunk.length() <= 50);
        }
        assertEquals(reply, String.join(" ", chunks));
    }
}
