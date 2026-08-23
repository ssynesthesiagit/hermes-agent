package com.hermes.gatewayclient;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Splits long bot replies at natural boundaries for Android's TTS limit. */
final class SpeechChunker {

    private SpeechChunker() {}

    static List<String> split(String text, int maximumLength) {
        if (text == null || text.trim().isEmpty()) {
            return Collections.emptyList();
        }
        if (maximumLength < 32) {
            throw new IllegalArgumentException("maximumLength must be at least 32");
        }

        String remaining = text.trim();
        List<String> chunks = new ArrayList<>();
        while (remaining.length() > maximumLength) {
            int boundary = bestBoundary(remaining, maximumLength);
            chunks.add(remaining.substring(0, boundary).trim());
            remaining = remaining.substring(boundary).trim();
        }
        if (!remaining.isEmpty()) {
            chunks.add(remaining);
        }
        return chunks;
    }

    private static int bestBoundary(String value, int maximumLength) {
        int minimumUsefulBoundary = maximumLength / 2;
        String[] boundaries = {"\n\n", ". ", "? ", "! ", "; ", ", ", " "};
        for (String token : boundaries) {
            int index = value.lastIndexOf(token, maximumLength);
            if (index >= minimumUsefulBoundary) {
                return index + token.length();
            }
        }
        return maximumLength;
    }
}
