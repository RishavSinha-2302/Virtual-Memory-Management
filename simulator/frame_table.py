"""
Physical memory — frame table and free-list management.

The first ``non_paged_frames`` frames are permanently pinned and
represent the non-paged pool (kernel data, device drivers, etc.).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

from simulator.config import SimConfig


class FrameState(Enum):
    FREE = "free"
    ALLOCATED = "allocated"
    NON_PAGED = "non_paged"       # pinned kernel memory
    PAGE_TABLE = "page_table"     # used for a page-table page


@dataclass
class FrameTableEntry:
    """Metadata for a single physical frame."""

    frame_id: int = 0
    state: FrameState = FrameState.FREE
    owning_pid: Optional[int] = None
    virtual_page: Optional[int] = None
    dirty: bool = False
    accessed: bool = False          # mirrored from PTE R-bit
    pin_count: int = 0              # >0 means frame is locked
    last_access_time: int = 0       # virtual-time stamp
    label: str = ""                 # symbolic content label

    # ── helpers ──────────────────────────────────────────────────
    @property
    def is_pinned(self) -> bool:
        return self.pin_count > 0 or self.state == FrameState.NON_PAGED

    def clear(self) -> None:
        self.state = FrameState.FREE
        self.owning_pid = None
        self.virtual_page = None
        self.dirty = False
        self.accessed = False
        self.pin_count = 0
        self.last_access_time = 0
        self.label = ""

    def to_dict(self) -> dict:
        return {
            "frame_id": self.frame_id,
            "state": self.state.value,
            "owning_pid": self.owning_pid,
            "virtual_page": self.virtual_page,
            "dirty": self.dirty,
            "accessed": self.accessed,
            "pin_count": self.pin_count,
            "last_access_time": self.last_access_time,
            "label": self.label,
            "is_pinned": self.is_pinned,
        }


class FrameTable:
    """
    Manages all physical frames.

    Frames 0 .. non_paged_frames-1 are reserved for the non-paged pool
    and are never available for user-space allocation.
    """

    def __init__(self, config: SimConfig):
        self.config = config
        self.frames: List[FrameTableEntry] = []
        self.free_list: deque[int] = deque()

        # Initialise frames
        for i in range(config.total_frames):
            entry = FrameTableEntry(frame_id=i)
            if i < config.non_paged_frames:
                entry.state = FrameState.NON_PAGED
                entry.pin_count = 1
                entry.label = f"kernel_nonpaged_{i}"
            else:
                entry.state = FrameState.FREE
                self.free_list.append(i)
            self.frames.append(entry)

    # ── Allocation ──────────────────────────────────────────────────
    def has_free_frame(self) -> bool:
        return len(self.free_list) > 0

    def allocate_frame(
        self,
        pid: int,
        vpn: int,
        label: str = "",
        state: FrameState = FrameState.ALLOCATED,
    ) -> Optional[int]:
        """
        Allocate a free frame.  Returns frame ID or ``None`` if the
        free list is empty (caller must invoke page replacement first).
        """
        if not self.free_list:
            return None
        fid = self.free_list.popleft()
        f = self.frames[fid]
        f.state = state
        f.owning_pid = pid
        f.virtual_page = vpn
        f.dirty = False
        f.accessed = True
        f.pin_count = 0
        f.label = label or f"P{pid}:VP{vpn}"
        return fid

    def free_frame(self, frame_id: int) -> None:
        """Return a frame to the free list."""
        f = self.frames[frame_id]
        if f.is_pinned:
            raise RuntimeError(f"Cannot free pinned frame {frame_id}")
        f.clear()
        self.free_list.append(frame_id)

    def get(self, frame_id: int) -> FrameTableEntry:
        return self.frames[frame_id]

    # ── Queries ─────────────────────────────────────────────────────
    @property
    def free_count(self) -> int:
        return len(self.free_list)

    @property
    def allocated_count(self) -> int:
        return sum(
            1 for f in self.frames
            if f.state == FrameState.ALLOCATED
        )

    @property
    def non_paged_count(self) -> int:
        return sum(
            1 for f in self.frames
            if f.state == FrameState.NON_PAGED
        )

    def get_frames_for_process(self, pid: int) -> List[FrameTableEntry]:
        return [f for f in self.frames if f.owning_pid == pid and f.state == FrameState.ALLOCATED]

    # ── Serialisation ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "total": self.config.total_frames,
            "free": self.free_count,
            "allocated": self.allocated_count,
            "non_paged": self.non_paged_count,
            "frames": [f.to_dict() for f in self.frames],
        }
