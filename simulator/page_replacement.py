"""
WSClock (Working Set Clock) page replacement algorithm.

Combines the Working Set concept with the Clock (second-chance)
scanning approach.  This is closely modelled on the algorithm used
by Windows NT's memory manager:

  1. Maintain a circular list of all resident (allocated) frames.
  2. On eviction request, spin the clock hand:
     a. R-bit set → clear it, record current time, advance.
     b. R-bit clear, within working-set window (τ) → skip, advance.
     c. R-bit clear, outside τ, clean → **evict immediately**.
     d. R-bit clear, outside τ, dirty → schedule write-back, advance.
  3. If a full revolution finds no victim, pick the oldest clean
     frame; if none, pick the oldest dirty whose write-back has
     "completed" (simulated).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from simulator.config import SimConfig
from simulator.frame_table import FrameTable, FrameState


@dataclass
class ClockEntry:
    """An element in the circular list."""
    frame_id: int


class WSClockReplacer:
    """WSClock page-replacement algorithm."""

    def __init__(self, config: SimConfig, frame_table: FrameTable):
        self.config = config
        self.frame_table = frame_table
        self.tau = config.working_set_window

        self._ring: List[ClockEntry] = []
        self._hand: int = 0

        # Frames whose dirty write-back has been "scheduled"
        self._pending_writebacks: set[int] = set()

        # Stats
        self.scans: int = 0
        self.evictions: int = 0
        self.dirty_evictions: int = 0
        self.clean_evictions: int = 0

    # ── Ring management ─────────────────────────────────────────────
    def add_frame(self, frame_id: int) -> None:
        """Add a newly-allocated frame to the clock ring."""
        if not any(e.frame_id == frame_id for e in self._ring):
            self._ring.append(ClockEntry(frame_id=frame_id))

    def remove_frame(self, frame_id: int) -> None:
        """Remove a freed frame from the clock ring."""
        idx = next(
            (i for i, e in enumerate(self._ring) if e.frame_id == frame_id),
            None,
        )
        if idx is not None:
            self._ring.pop(idx)
            if self._hand >= len(self._ring) and self._ring:
                self._hand = 0
        self._pending_writebacks.discard(frame_id)

    # ── Victim selection ────────────────────────────────────────────
    def select_victim(self, current_time: int) -> Optional[Tuple[int, bool]]:
        """
        Select a frame to evict.

        Returns ``(frame_id, was_dirty)`` or ``None`` if no candidate
        is found (should not happen if ring is non-empty and there are
        non-pinned frames).
        """
        if not self._ring:
            return None

        n = len(self._ring)
        start = self._hand
        best_clean: Optional[Tuple[int, int]] = None      # (frame_id, age)
        best_dirty: Optional[Tuple[int, int]] = None

        for _ in range(2 * n):            # allow up to two full revolutions
            self.scans += 1
            entry = self._ring[self._hand]
            fid = entry.frame_id
            frame = self.frame_table.get(fid)

            # Skip pinned / non-paged frames
            if frame.is_pinned or frame.state != FrameState.ALLOCATED:
                self._advance_hand()
                continue

            age = current_time - frame.last_access_time

            # Step A: referenced recently → clear R-bit, update time
            if frame.accessed:
                frame.accessed = False
                frame.last_access_time = current_time
                self._advance_hand()
                continue

            # Step B: within working-set window → skip
            if age < self.tau:
                self._advance_hand()
                continue

            # Step C: outside τ, clean → immediate eviction
            if not frame.dirty:
                victim = fid
                self._advance_hand()
                self.evictions += 1
                self.clean_evictions += 1
                return (victim, False)

            # Step D: outside τ, dirty → schedule write-back
            if fid not in self._pending_writebacks:
                self._pending_writebacks.add(fid)

            # Track best dirty candidate
            if best_dirty is None or age > best_dirty[1]:
                best_dirty = (fid, age)

            self._advance_hand()

        # ── Fallback: full revolution without clean victim ──────────
        # Pick oldest dirty page whose write-back was "scheduled"
        if best_dirty is not None:
            victim = best_dirty[0]
            self._pending_writebacks.discard(victim)
            self.evictions += 1
            self.dirty_evictions += 1
            return (victim, True)

        # Absolute fallback — any non-pinned frame
        for entry in self._ring:
            f = self.frame_table.get(entry.frame_id)
            if not f.is_pinned and f.state == FrameState.ALLOCATED:
                self.evictions += 1
                was_dirty = f.dirty
                if was_dirty:
                    self.dirty_evictions += 1
                else:
                    self.clean_evictions += 1
                return (entry.frame_id, was_dirty)

        return None

    def _advance_hand(self) -> None:
        if self._ring:
            self._hand = (self._hand + 1) % len(self._ring)

    # ── Serialisation ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "tau": self.tau,
            "ring_size": len(self._ring),
            "hand_position": self._hand,
            "pending_writebacks": list(self._pending_writebacks),
            "evictions": self.evictions,
            "clean_evictions": self.clean_evictions,
            "dirty_evictions": self.dirty_evictions,
            "scans": self.scans,
            "ring": [e.frame_id for e in self._ring],
        }
