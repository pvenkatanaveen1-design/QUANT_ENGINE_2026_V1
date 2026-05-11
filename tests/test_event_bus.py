"""
tests/test_event_bus.py — Tests for core/event_bus.py

Run: pytest tests/test_event_bus.py -v
"""

import sys
import time
import threading
from pathlib import Path

# ── Path bootstrap ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from core.event_bus import EventBus, EventType, Event, SYNCHRONOUS_EVENTS


@pytest.fixture
def bus():
    """Fresh EventBus for each test.  Avoids singleton state pollution."""
    b = EventBus(max_workers=2)
    yield b
    b.shutdown()


class TestEventBusSubscribe:
    def test_subscribe_and_count(self, bus):
        def handler(e): pass
        bus.subscribe(EventType.HEARTBEAT, handler)
        assert bus.subscriber_count(EventType.HEARTBEAT) == 1

    def test_unsubscribe(self, bus):
        def handler(e): pass
        bus.subscribe(EventType.HEARTBEAT, handler)
        bus.unsubscribe(EventType.HEARTBEAT, handler)
        assert bus.subscriber_count(EventType.HEARTBEAT) == 0

    def test_multiple_handlers(self, bus):
        def h1(e): pass
        def h2(e): pass
        bus.subscribe(EventType.HEARTBEAT, h1)
        bus.subscribe(EventType.HEARTBEAT, h2)
        assert bus.subscriber_count(EventType.HEARTBEAT) == 2


class TestEventBusPublish:
    def test_publish_no_subscribers_returns_zero(self, bus):
        count = bus.publish(EventType.HEARTBEAT, {})
        assert count == 0

    def test_publish_calls_handler(self, bus):
        received = []
        def handler(e):
            received.append(e)
        bus.subscribe(EventType.HEARTBEAT, handler)
        bus.publish(EventType.HEARTBEAT, {"alive": True}, source="test")
        # Wait for async dispatch
        time.sleep(0.2)
        assert len(received) == 1
        assert received[0].event_type == EventType.HEARTBEAT
        assert received[0].payload == {"alive": True}
        assert received[0].source == "test"

    def test_publish_returns_handler_count(self, bus):
        def h1(e): pass
        def h2(e): pass
        bus.subscribe(EventType.HEARTBEAT, h1)
        bus.subscribe(EventType.HEARTBEAT, h2)
        count = bus.publish(EventType.HEARTBEAT, {})
        assert count == 2

    def test_correlation_id_passed_through(self, bus):
        received = []
        def handler(e):
            received.append(e.correlation_id)
        bus.subscribe(EventType.SIGNAL_GENERATED, handler)
        bus.publish(EventType.SIGNAL_GENERATED, {}, correlation_id="test-uuid-123")
        time.sleep(0.2)
        assert "test-uuid-123" in received


class TestSynchronousEvents:
    def test_kill_switch_is_synchronous(self, bus):
        """KILL_SWITCH handler must complete before publish() returns."""
        completed = []

        def handler(e):
            # Simulate some work
            time.sleep(0.05)
            completed.append("done")

        bus.subscribe(EventType.KILL_SWITCH, handler)
        bus.publish(EventType.KILL_SWITCH, {"reason": "test"})

        # Should be done immediately (synchronous, no sleep needed)
        assert "done" in completed, "KILL_SWITCH must run synchronously"

    def test_drawdown_limit_is_synchronous(self, bus):
        completed = []
        def handler(e):
            completed.append("done")
        bus.subscribe(EventType.DRAWDOWN_LIMIT, handler)
        bus.publish(EventType.DRAWDOWN_LIMIT, {"dd_pct": 5.0})
        assert "done" in completed


class TestExceptionHandling:
    def test_bad_handler_does_not_crash_bus(self, bus):
        """A handler that raises should not stop other handlers."""
        results = []

        def bad_handler(e):
            raise ValueError("intentional error")

        def good_handler(e):
            results.append("good")

        bus.subscribe(EventType.HEARTBEAT, bad_handler)
        bus.subscribe(EventType.HEARTBEAT, good_handler)
        bus.publish(EventType.HEARTBEAT, {})
        time.sleep(0.3)
        assert "good" in results, "Good handler must fire even when bad handler raises"

    def test_failure_count_increments(self, bus):
        def bad_handler(e):
            raise RuntimeError("boom")
        bus.subscribe(EventType.HEARTBEAT, bad_handler)
        bus.publish(EventType.HEARTBEAT, {})
        time.sleep(0.3)
        diag = bus.get_diagnostics()
        assert diag["failure_count"] >= 1


class TestDiagnostics:
    def test_diagnostics_structure(self, bus):
        diag = bus.get_diagnostics()
        required_keys = ["publish_count", "failure_count", "subscriber_count",
                         "subscribers", "metrics", "recent_events"]
        for key in required_keys:
            assert key in diag, f"Missing key: {key}"

    def test_publish_count_increments(self, bus):
        bus.publish(EventType.HEARTBEAT, {})
        bus.publish(EventType.HEARTBEAT, {})
        diag = bus.get_diagnostics()
        assert diag["publish_count"] >= 2

    def test_event_history_recorded(self, bus):
        bus.publish(EventType.HEARTBEAT, {}, source="test_source")
        time.sleep(0.1)
        diag = bus.get_diagnostics()
        events = diag["recent_events"]
        assert any(e["source"] == "test_source" for e in events)


class TestConcurrency:
    def test_concurrent_publish_thread_safe(self, bus):
        """Multiple threads publishing simultaneously should not crash."""
        results = []
        errors  = []

        def handler(e):
            results.append(1)

        bus.subscribe(EventType.MARKET_DATA, handler)

        def publish_many():
            for _ in range(50):
                try:
                    bus.publish(EventType.MARKET_DATA, {"tick": True})
                except Exception as e:
                    errors.append(str(e))

        threads = [threading.Thread(target=publish_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.5)

        assert not errors, f"Thread-safety errors: {errors}"
        assert len(results) == 200  # 4 threads × 50 publishes
