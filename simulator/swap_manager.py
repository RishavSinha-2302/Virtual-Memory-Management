"""
Swap space manager.

Maintains a fixed-size array of swap slots, each capable of holding
one page's symbolic label.  Tracks I/O stats and pending writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

from simulator.config import SimConfig


class SwapSlotState(Enum):
    FREE = "free"
    USED = "used"


@dataclass
class SwapSlot:
    slot_id: int = 0
    state: SwapSlotState = SwapSlotState.FREE
    owning_pid: Optional[int] = None
    virtual_page: Optional[int] = None
    label: str = ""                 # symbolic page content

    def clear(self) -> None:
        self.state = SwapSlotState.FREE
        self.owning_pid = None
        self.virtual_page = None
        self.label = ""

    def to_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "state": self.state.value,
            "owning_pid": self.owning_pid,
            "virtual_page": self.virtual_page,
            "label": self.label,
        }


class SwapManager:
    """Manages swap space as a flat array of slots."""

    def __init__(self, config: SimConfig):
        self.config = config
        self.slots: List[SwapSlot] = [
            SwapSlot(slot_id=i) for i in range(config.swap_slots)
        ]
        self._free_ids: List[int] = list(range(config.swap_slots))

        # I/O stats
        self.total_writes: int = 0
        self.total_reads: int = 0

    # ── Allocation ──────────────────────────────────────────────────
    def has_free_slot(self) -> bool:
        return len(self._free_ids) > 0

    def allocate_slot(self, pid: int, vpn: int) -> Optional[int]:
        """Allocate a swap slot.  Returns slot ID or ``None``."""
        if not self._free_ids:
            return None
        sid = self._free_ids.pop(0)
        s = self.slots[sid]
        s.state = SwapSlotState.USED
        s.owning_pid = pid
        s.virtual_page = vpn
        return sid

    def free_slot(self, slot_id: int) -> None:
        s = self.slots[slot_id]
        s.clear()
        self._free_ids.append(slot_id)

    # ── I/O operations (simulated) ──────────────────────────────────
    def write_page(self, slot_id: int, label: str) -> None:
        """Write a page's label to swap (simulates disk write)."""
        self.slots[slot_id].label = label
        self.total_writes += 1

    def read_page(self, slot_id: int) -> str:
        """Read a page's label from swap (simulates disk read)."""
        self.total_reads += 1
        return self.slots[slot_id].label

    # ── Queries ─────────────────────────────────────────────────────
    @property
    def free_count(self) -> int:
        return len(self._free_ids)

    @property
    def used_count(self) -> int:
        return self.config.swap_slots - self.free_count

    def get_slots_for_process(self, pid: int) -> List[SwapSlot]:
        return [s for s in self.slots if s.owning_pid == pid and s.state == SwapSlotState.USED]

    # ── Serialisation ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "total": self.config.swap_slots,
            "free": self.free_count,
            "used": self.used_count,
            "total_writes": self.total_writes,
            "total_reads": self.total_reads,
            "slots": [s.to_dict() for s in self.slots],
        }
