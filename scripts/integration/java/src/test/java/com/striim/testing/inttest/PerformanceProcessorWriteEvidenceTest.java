package com.striim.testing.inttest;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Arrays;
import java.util.List;

import org.junit.jupiter.api.Test;

import com.webaction.event.Event;
import com.webaction.proc.events.WAEvent;

/**
 * What a target perf iteration reports as evidence that the writer wrote something. A writer emits
 * nothing, so without these fields a writer that dropped every event reported a throughput and
 * passed; the Python runner fails an iteration on them (PERF_SPEC.md §4, §6).
 */
class PerformanceProcessorWriteEvidenceTest {

    /** Accepts every event and emits nothing; acknowledges what it accepted at each flush. */
    private static class FakeWriter implements EventDriver {
        private final boolean acknowledges;
        private final boolean acksAnything;
        private final String processed;
        private long accepted;
        private long acked;

        FakeWriter(boolean acknowledges, boolean acksAnything, String processed) {
            this.acknowledges = acknowledges;
            this.acksAnything = acksAnything;
            this.processed = processed;
        }

        @Override
        public List<WAEvent> processEvent(Event event) {
            accepted++;
            return List.of();
        }

        @Override
        public void endOfWindow() {
            if (acksAnything) {
                acked = accepted;
            }
        }

        @Override
        public boolean writes() {
            return true;
        }

        @Override
        public long acknowledgedEvents() {
            return acknowledges ? acked : -1;
        }

        @Override
        public String publishedProcessed() {
            return processed;
        }

        @Override
        public void close() {
        }
    }

    /** A processor: emits nothing here, and is not a writer. */
    private static final class Processor implements EventDriver {
        @Override
        public List<WAEvent> processEvent(Event event) {
            return List.of();
        }

        @Override
        public void close() {
        }
    }

    private static PerformanceProcessor.PerfRequest request(int warmupRuns, int runSize) {
        PerformanceProcessor.PerfRequest r = new PerformanceProcessor.PerfRequest();
        r.warmupRuns = warmupRuns;
        r.runSize = runSize;
        r.correctnessMode = "disabled";
        // A non-null source skips per-record materialization, so the templates can be placeholders.
        r.source = new SourceSpec();
        return r;
    }

    private static final List<Event> TWO_RECORDS = Arrays.asList(null, null);

    @Test
    void theAckCountIsTheMeasuredWindowsOwnNotTheWarmups() {
        PerformanceProcessor.PerfResult result = new PerformanceProcessor.PerfResult();
        Exception failure = PerformanceProcessor.runWarmupAndMeasured(request(2, 3), TWO_RECORDS,
                new FakeWriter(true, true, "10"), result);

        assertNull(failure);
        assertEquals(6, result.measuredInputEvents);
        assertEquals(6L, result.measuredAckedEvents, "4 warmup acks are not the window's");
        assertEquals("10", result.publishedProcessed);
    }

    @Test
    void aWriterThatAcknowledgesNothingReportsZero() {
        PerformanceProcessor.PerfResult result = new PerformanceProcessor.PerfResult();
        Exception failure = PerformanceProcessor.runWarmupAndMeasured(request(1, 3), TWO_RECORDS,
                new FakeWriter(true, false, null), result);

        assertNull(failure, "reported, not thrown: the runner decides the iteration");
        assertEquals(0L, result.measuredAckedEvents);
        assertNull(result.publishedProcessed);
    }

    @Test
    void aWriterWithoutTheAcknowledgeableMarkerReportsNoAckCount() {
        PerformanceProcessor.PerfResult result = new PerformanceProcessor.PerfResult();
        PerformanceProcessor.runWarmupAndMeasured(request(0, 2), TWO_RECORDS,
                new FakeWriter(false, false, "4"), result);

        assertNull(result.measuredAckedEvents);
        assertEquals("4", result.publishedProcessed);
    }

    @Test
    void aMonitorReadThatThrowsFailsTheIteration() {
        PerformanceProcessor.PerfResult result = new PerformanceProcessor.PerfResult();
        Exception failure = PerformanceProcessor.runWarmupAndMeasured(request(0, 2), TWO_RECORDS,
                new FakeWriter(true, true, null) {
                    @Override
                    public String publishedProcessed() {
                        throw new IllegalStateException("publishMonitorEvents blew up");
                    }
                }, result);

        assertNotNull(failure);
        assertTrue(result.errorMessage.contains("publishMonitorEvents blew up"), result.errorMessage);
    }

    @Test
    void aProcessorReportsNoWriterEvidence() {
        PerformanceProcessor.PerfResult result = new PerformanceProcessor.PerfResult();
        PerformanceProcessor.runWarmupAndMeasured(request(1, 2), TWO_RECORDS, new Processor(), result);

        assertNull(result.measuredAckedEvents);
        assertNull(result.publishedProcessed);
    }
}
