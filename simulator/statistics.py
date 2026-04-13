"""
Centralised statistics collector.

Aggregates counters from all subsystems and maintains time-series
snapshots for the dashboard charts.
"""

from __future__ import annotations

from typing import List, Dict, Optional

from simulator.config import SimConfig


class SimulatorStats:
    """Collects and exposes simulation-wide statistics."""

    def __init__(self, config: SimConfig):
        self.config = config

        # Global counters
        self.total_accesses: int = 0
        self.total_page_faults: int = 0
        self.total_tlb_hits: int = 0
        self.total_tlb_misses: int = 0
        self.total_swap_reads: int = 0
        self.total_swap_writes: int = 0
        self.total_evictions: int = 0
        self.total_context_switches: int = 0

        # Per-process
        self.per_process: Dict[int, dict] = {}

        # Time-series
        self.timeline: List[dict] = []

    def record_access(
        self,
        pid: int,
        vpn: int,
        access_type: str,       # "read" or "write"
        tlb_hit: bool,
        page_fault: bool,
    ) -> None:
        self.total_accesses += 1
        if tlb_hit:
            self.total_tlb_hits += 1
        else:
            self.total_tlb_misses += 1
        if page_fault:
            self.total_page_faults += 1

        # Per-process
        if pid not in self.per_process:
            self.per_process[pid] = {
                "accesses": 0, "faults": 0,
                "tlb_hits": 0, "tlb_misses": 0,
            }
        pp = self.per_process[pid]
        pp["accesses"] += 1
        if page_fault:
            pp["faults"] += 1
        if tlb_hit:
            pp["tlb_hits"] += 1
        else:
            pp["tlb_misses"] += 1

    def record_swap_read(self) -> None:
        self.total_swap_reads += 1

    def record_swap_write(self) -> None:
        self.total_swap_writes += 1

    def record_eviction(self) -> None:
        self.total_evictions += 1

    def record_context_switch(self) -> None:
        self.total_context_switches += 1

    def take_snapshot(self, time: int, extra: Optional[dict] = None) -> None:
        """Append a data point to the timeline."""
        total = self.total_tlb_hits + self.total_tlb_misses
        snap = {
            "time": time,
            "accesses": self.total_accesses,
            "page_faults": self.total_page_faults,
            "tlb_hit_rate": round(self.total_tlb_hits / total, 4) if total > 0 else 0,
            "swap_reads": self.total_swap_reads,
            "swap_writes": self.total_swap_writes,
            "evictions": self.total_evictions,
        }
        if extra:
            snap.update(extra)
        self.timeline.append(snap)

    @property
    def page_fault_rate(self) -> float:
        if self.total_accesses == 0:
            return 0.0
        return self.total_page_faults / self.total_accesses

    @property
    def tlb_hit_rate(self) -> float:
        total = self.total_tlb_hits + self.total_tlb_misses
        if total == 0:
            return 0.0
        return self.total_tlb_hits / total

    # ── Serialisation ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "total_accesses": self.total_accesses,
            "total_page_faults": self.total_page_faults,
            "page_fault_rate": round(self.page_fault_rate, 4),
            "tlb_hits": self.total_tlb_hits,
            "tlb_misses": self.total_tlb_misses,
            "tlb_hit_rate": round(self.tlb_hit_rate, 4),
            "swap_reads": self.total_swap_reads,
            "swap_writes": self.total_swap_writes,
            "evictions": self.total_evictions,
            "context_switches": self.total_context_switches,
            "per_process": self.per_process,
        }

    def get_timeline(self, last_n: int = 100) -> List[dict]:
        return self.timeline[-last_n:]
