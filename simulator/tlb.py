"""
Translation Lookaside Buffer (TLB).

Fully-associative, LRU-eviction cache that stores recent
virtual-page → physical-frame translations.  Flushed on context
switch (per-process entries) to model the real behaviour.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, List

from simulator.config import SimConfig


@dataclass
class TLBEntry:
    process_id: int
    virtual_page: int
    frame_number: int
    dirty: bool = False
    accessed: bool = False
    valid: bool = True

    def to_dict(self) -> dict:
        return {
            "process_id": self.process_id,
            "virtual_page": self.virtual_page,
            "frame_number": self.frame_number,
            "dirty": self.dirty,
            "accessed": self.accessed,
            "valid": self.valid,
        }


class TLB:
    """
    Fixed-size, fully-associative TLB backed by an ``OrderedDict``
    for LRU eviction.

    Keys are ``(process_id, virtual_page)`` tuples.
    """

    def __init__(self, config: SimConfig):
        self.config = config
        self.capacity = config.tlb_size
        self._cache: OrderedDict[tuple, TLBEntry] = OrderedDict()

        # Stats
        self.hits: int = 0
        self.misses: int = 0

    # ── Lookup ──────────────────────────────────────────────────────
    def lookup(self, pid: int, vpn: int) -> Optional[TLBEntry]:
        """
        Return the TLB entry for *(pid, vpn)* if present and valid,
        else ``None``.  Moves the entry to MRU on hit.
        """
        key = (pid, vpn)
        entry = self._cache.get(key)
        if entry is not None and entry.valid:
            self.hits += 1
            self._cache.move_to_end(key)  # mark as MRU
            entry.accessed = True
            return entry
        self.misses += 1
        return None

    # ── Insert / update ─────────────────────────────────────────────
    def insert(
        self,
        pid: int,
        vpn: int,
        frame: int,
        dirty: bool = False,
    ) -> None:
        """Insert or update a TLB entry.  Evicts LRU if full."""
        key = (pid, vpn)
        if key in self._cache:
            # Update existing
            e = self._cache[key]
            e.frame_number = frame
            e.dirty = dirty
            e.valid = True
            e.accessed = True
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self.capacity:
                self._cache.popitem(last=False)  # evict LRU
            self._cache[key] = TLBEntry(
                process_id=pid,
                virtual_page=vpn,
                frame_number=frame,
                dirty=dirty,
                accessed=True,
            )

    # ── Invalidation ────────────────────────────────────────────────
    def invalidate(self, pid: int, vpn: int) -> None:
        """Invalidate a single entry."""
        key = (pid, vpn)
        if key in self._cache:
            del self._cache[key]

    def flush(self, pid: Optional[int] = None) -> int:
        """
        Flush TLB entries.  If *pid* is given, flush only that
        process's entries (simulates context-switch behaviour).
        Returns the number of entries flushed.
        """
        if pid is None:
            count = len(self._cache)
            self._cache.clear()
            return count
        keys_to_remove = [k for k in self._cache if k[0] == pid]
        for k in keys_to_remove:
            del self._cache[k]
        return len(keys_to_remove)

    # ── stats helpers ───────────────────────────────────────────────
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def size(self) -> int:
        return len(self._cache)

    def reset_stats(self) -> None:
        self.hits = 0
        self.misses = 0

    # ── Serialisation ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "capacity": self.capacity,
            "size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "entries": [e.to_dict() for e in self._cache.values()],
        }
