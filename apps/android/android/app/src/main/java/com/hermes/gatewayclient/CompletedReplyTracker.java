package com.hermes.gatewayclient;

/** Suppresses old and duplicate replies while allowing a completed new reply once. */
final class CompletedReplyTracker {

    static final class Snapshot {
        final String contextId;
        final String replyId;
        final String text;
        final boolean streaming;

        Snapshot(String contextId, String replyId, String text, boolean streaming) {
            this.contextId = contextId == null ? "" : contextId;
            this.replyId = replyId == null ? "" : replyId;
            this.text = text == null ? "" : text.trim();
            this.streaming = streaming;
        }

        boolean hasReply() {
            return !replyId.isEmpty() && !text.isEmpty();
        }
    }

    private final long quietPeriodMillis;
    private String contextId;
    private String consumedReplyId;
    private String candidateReplyId;
    private long candidateSinceMillis;

    CompletedReplyTracker(long quietPeriodMillis) {
        if (quietPeriodMillis < 0) {
            throw new IllegalArgumentException("quietPeriodMillis must be non-negative");
        }
        this.quietPeriodMillis = quietPeriodMillis;
    }

    void prime(Snapshot snapshot) {
        contextId = snapshot == null ? null : snapshot.contextId;
        consumedReplyId = snapshot != null && snapshot.hasReply() && !snapshot.streaming
            ? snapshot.replyId
            : null;
        clearCandidate();
    }

    String observe(Snapshot snapshot, long nowMillis) {
        if (snapshot == null) {
            return null;
        }
        if (contextId == null || !contextId.equals(snapshot.contextId)) {
            prime(snapshot);
            return null;
        }
        if (!snapshot.hasReply()) {
            consumedReplyId = null;
            clearCandidate();
            return null;
        }
        if (snapshot.streaming) {
            clearCandidate();
            return null;
        }
        if (snapshot.replyId.equals(consumedReplyId)) {
            clearCandidate();
            return null;
        }
        if (!snapshot.replyId.equals(candidateReplyId)) {
            candidateReplyId = snapshot.replyId;
            candidateSinceMillis = nowMillis;
            return null;
        }
        if (nowMillis - candidateSinceMillis < quietPeriodMillis) {
            return null;
        }

        consumedReplyId = snapshot.replyId;
        clearCandidate();
        return snapshot.text;
    }

    void markConsumed(Snapshot snapshot) {
        if (snapshot == null) {
            return;
        }
        contextId = snapshot.contextId;
        consumedReplyId = snapshot.replyId;
        clearCandidate();
    }

    private void clearCandidate() {
        candidateReplyId = null;
        candidateSinceMillis = 0;
    }
}
