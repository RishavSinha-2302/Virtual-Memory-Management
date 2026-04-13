"""
Thrashing detection using a sliding-window page-fault-rate monitor.

Also tracks per-process working-set sizes and the current degree of
multiprogramming to provide actionable recommendations.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional, Tuple

from simulator.config import SimConfig


class ThrashingDetector:
    """Monitors system-wide page-fault rate and flags thrashing."""

    def __init__(self, config: SimConfig):
        self.config = config
        self.threshold = config.thrashing_threshold
        self.window_size = config.thrashing_window

        # Sliding window: each entry is (timestamp, was_fault)
        self._window: Deque[Tuple[int, bool]] = deque()
        self._fault_count_in_window: int = 0
        self._total_in_window: int = 0

        # History for charts
        self.history: List[dict] = []
        self._is_thrashing: bool = False

        # Cumulative
        self.total_faults: int = 0
        self.total_accesses: int = 0

    def record_access(self, timestamp: int, was_fault: bool) -> None:
        """Record one memory access (faulting or not)."""
        self._window.append((timestamp, was_fault))
        self._total_in_window += 1
        if was_fault:
            self._fault_count_in_window += 1
            self.total_faults += 1
        self.total_accesses += 1

        # Trim window to size
        while self._total_in_window > self.window_size:
            _, old_fault = self._window.popleft()
            self._total_in_window -= 1
            if old_fault:
                self._fault_count_in_window -= 1

        rate = self.fault_rate
        was_thrashing = self._is_thrashing
        self._is_thrashing = rate >= self.threshold

        self.history.append({
            "time": timestamp,
            "fault_rate": round(rate, 4),
            "is_thrashing": self._is_thrashing,
        })

    @property
    def fault_rate(self) -> float:
        if self._total_in_window == 0:
            return 0.0
        return self._fault_count_in_window / self._total_in_window

    @property
    def is_thrashing(self) -> bool:
        return self._is_thrashing

    def get_recommendation(
        self,
        process_working_sets: dict[int, int],
        available_frames: int,
    ) -> str:
        """Produce a human-readable recommendation."""
        total_ws = sum(process_working_sets.values())
        if not self._is_thrashing:
            return "System is healthy — no thrashing detected."

        if total_ws > available_frames:
            # Find the process with the largest working set
            biggest_pid = max(process_working_sets, key=process_working_sets.get)  # type: ignore
            return (
                f"THRASHING DETECTED — combined working sets ({total_ws} pages) "
                f"exceed available frames ({available_frames}).  "
                f"Recommend suspending process {biggest_pid} "
                f"(WS size {process_working_sets[biggest_pid]})."
            )
        return (
            "THRASHING DETECTED — high page-fault rate.  Consider "
            "increasing physical memory or reducing the workload."
        )

    # ── Serialisation ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "fault_rate": round(self.fault_rate, 4),
            "is_thrashing": self._is_thrashing,
            "threshold": self.threshold,
            "window_size": self.window_size,
            "total_faults": self.total_faults,
            "total_accesses": self.total_accesses,
            "history": self.history[-100:],  # last 100 points
        }
