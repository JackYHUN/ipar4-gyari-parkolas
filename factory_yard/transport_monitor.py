"""Bounded host-clock traffic observations; never a maximum-capacity estimate."""
from collections import deque
import math


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class TransportMonitor:
    INTERVAL_NS = 1_000_000_000
    WINDOW_NS = 5_000_000_000
    HISTORY_LIMIT = 120

    def __init__(self):
        self.reset()

    def reset(self, reason="new_run"):
        self._points = deque(maxlen=self.HISTORY_LIMIT)
        self._origin_ns = None
        self._segment = 0 if reason == "new_run" else getattr(self, "_segment", 0) + 1
        self.reset_reason = reason

    def due(self, now_ns):
        if _count(now_ns) is None or not self._points:
            return True
        delta = now_ns - self._points[-1]["clock_ns"]
        return delta < 0 or delta >= self.INTERVAL_NS

    def observe(self, now_ns, received, dequeued, queue_messages, snapshot_age_s=None):
        if _count(now_ns) is None:
            self.reset("invalid_clock")
            return
        if not self.due(now_ns):
            return
        received, dequeued, queue_messages = map(_count, (received, dequeued, queue_messages))
        if self._points:
            previous = self._points[-1]
            if now_ns < previous["clock_ns"]:
                self.reset("clock_regression")
            elif any(value is not None and previous[key] is not None and value < previous[key]
                     for key, value in (("received_total", received), ("dequeued_total", dequeued))):
                self.reset("counter_reset")
        if self._origin_ns is None:
            self._origin_ns = now_ns
        age = (snapshot_age_s if isinstance(snapshot_age_s, (int, float))
               and not isinstance(snapshot_age_s, bool) and math.isfinite(snapshot_age_s)
               and snapshot_age_s >= 0 else None)
        point = dict(clock_ns=now_ns, segment=self._segment, elapsed_s=(now_ns - self._origin_ns) / 1e9,
            received_total=received, dequeued_total=dequeued, queue_messages=queue_messages,
            snapshot_age_s=age, window_s=None, arrival_rate_hz=None, dequeue_rate_hz=None,
            queue_growth_hz=None)
        # Rates never span a missing counter, regressed counter, or long
        # sampling gap. Queue observations themselves can still be retained.
        candidates = []
        if received is not None and dequeued is not None:
            next_clock = now_ns
            for old in reversed(self._points):
                if (now_ns - old["clock_ns"] > self.WINDOW_NS
                        or next_clock - old["clock_ns"] > 2 * self.INTERVAL_NS
                        or old["received_total"] is None or old["dequeued_total"] is None):
                    break
                candidates.append(old)
                next_clock = old["clock_ns"]
        if candidates:
            anchor = candidates[-1]
            seconds = (now_ns - anchor["clock_ns"]) / 1e9
            if seconds >= self.INTERVAL_NS / 1e9:
                point.update(window_s=seconds,
                    arrival_rate_hz=(received - anchor["received_total"]) / seconds,
                    dequeue_rate_hz=(dequeued - anchor["dequeued_total"]) / seconds)
                if queue_messages is not None and anchor["queue_messages"] is not None:
                    point["queue_growth_hz"] = (queue_messages - anchor["queue_messages"]) / seconds
        self._points.append(point)
        return {key: value for key, value in point.items() if key != "clock_ns"}

    def snapshot(self, now_ns):
        last = self._points[-1] if self._points else None
        age = ((now_ns - last["clock_ns"]) / 1e9 if last and _count(now_ns) is not None
               and now_ns >= last["clock_ns"] else None)
        status = "unavailable"
        if last and age is not None:
            status = "observed" if last["window_s"] is not None else "collecting"
            if last["received_total"] is None or last["dequeued_total"] is None:
                status = "unavailable"
            if age > 2.5:
                status = "stale"
        values = ("window_s", "arrival_rate_hz", "dequeue_rate_hz", "queue_messages", "queue_growth_hz")
        result = {key: last[key] if last else None for key in values}
        result.update(status=status, sample_age_s=age, history_limit=self.HISTORY_LIMIT,
            sample_interval_s=self.INTERVAL_NS / 1e9, window_target_s=self.WINDOW_NS / 1e9,
            reset_reason=self.reset_reason,
            clock="host_perf_counter", scope="current_run_bounded_in_memory_observations",
            rate_definition="counter delta / actual positive host-clock window; all subscribed topics, not maximum capacity",
            queue_definition="queue.qsize() at diagnostic sample; approximate while callback is active",
            history=[{key: value for key, value in point.items() if key != "clock_ns"}
                     for point in self._points])
        return result
