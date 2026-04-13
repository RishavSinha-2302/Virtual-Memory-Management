"""
4-level hierarchical page table, modelled after x86-64 paging.

Levels (real names → simulator equivalents):
  PML4 → Level 0
  PDPT → Level 1
  PD   → Level 2
  PT   → Level 3

Each level is a table with 2^bits_per_level entries.  Entries at levels
0-2 point to the next-level table; entries at level 3 (leaf) hold the
frame number or swap slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from simulator.config import SimConfig

# Symbolic level names matching x86-64
LEVEL_NAMES = ["PML4", "PDPT", "PD", "PT"]


@dataclass
class PageTableEntry:
    """A single entry in any page-table level."""

    present: bool = False          # P — page is resident in physical memory
    read_write: bool = True        # R/W — writable if True
    user_supervisor: bool = True   # U/S — user-mode accessible if True
    accessed: bool = False         # A — set by MMU on reference
    dirty: bool = False            # D — set by MMU on write (leaf only)
    no_execute: bool = False       # NX — non-executable

    frame_number: Optional[int] = None   # physical frame (if present)
    swap_slot: Optional[int] = None      # swap location (if swapped out)

    # For non-leaf entries: pointer to next-level table
    next_level: Optional["PageTableLevel"] = field(default=None, repr=False)

    # ── Helpers ─────────────────────────────────────────────────────
    def clear(self) -> None:
        self.present = False
        self.accessed = False
        self.dirty = False
        self.frame_number = None
        self.swap_slot = None
        self.next_level = None

    def to_dict(self, is_leaf: bool = False) -> dict:
        d: dict = {
            "present": self.present,
            "read_write": self.read_write,
            "user_supervisor": self.user_supervisor,
            "accessed": self.accessed,
            "dirty": self.dirty,
            "no_execute": self.no_execute,
        }
        if is_leaf:
            d["frame_number"] = self.frame_number
            d["swap_slot"] = self.swap_slot
        else:
            d["has_next_level"] = self.next_level is not None
        return d


class PageTableLevel:
    """One level of the page-table hierarchy — an array of entries."""

    def __init__(self, num_entries: int, level_index: int):
        self.level_index = level_index
        self.num_entries = num_entries
        self.entries: List[PageTableEntry] = [
            PageTableEntry() for _ in range(num_entries)
        ]

    def to_dict(self, config: SimConfig) -> dict:
        is_leaf = self.level_index == config.levels - 1
        children = {}
        for idx, entry in enumerate(self.entries):
            if entry.present or entry.swap_slot is not None or entry.next_level is not None:
                child: dict = entry.to_dict(is_leaf=is_leaf)
                if not is_leaf and entry.next_level is not None:
                    child["children"] = entry.next_level.to_dict(config)
                children[idx] = child
        return {
            "level": self.level_index,
            "level_name": LEVEL_NAMES[self.level_index] if self.level_index < len(LEVEL_NAMES) else f"L{self.level_index}",
            "entries": children,
        }


class PageTable:
    """
    Complete 4-level page table for a single process.

    The root table corresponds to PML4 (level 0).  Intermediate tables
    are allocated lazily when a mapping is first created.
    """

    def __init__(self, config: SimConfig):
        self.config = config
        self.root = PageTableLevel(config.entries_per_level[0], level_index=0)
        self._allocated_tables: int = 1  # count of table pages allocated

    # ── Core operations ─────────────────────────────────────────────
    def walk(
        self, vpn: int, allocate: bool = False
    ) -> Tuple[Optional[PageTableEntry], List[int]]:
        """
        Walk the page table for *vpn*.

        Returns ``(leaf_entry, indices)`` where *leaf_entry* is the PTE
        at the last level, or ``None`` if an intermediate table is
        missing and *allocate* is False.
        """
        indices = self.config.virtual_page_to_indices(vpn)
        current_level = self.root

        for depth in range(self.config.levels - 1):
            idx = indices[depth]
            entry = current_level.entries[idx]

            if entry.next_level is None:
                if not allocate:
                    return None, indices
                # Lazily allocate the next-level table
                next_num = self.config.entries_per_level[depth + 1]
                entry.next_level = PageTableLevel(next_num, level_index=depth + 1)
                entry.present = True        # intermediate node is "present"
                self._allocated_tables += 1

            current_level = entry.next_level

        # current_level is now the leaf (PT) table
        leaf_idx = indices[-1]
        return current_level.entries[leaf_idx], indices

    def map_page(
        self,
        vpn: int,
        frame: int,
        read_write: bool = True,
        user_supervisor: bool = True,
        no_execute: bool = False,
    ) -> PageTableEntry:
        """Create or update a leaf mapping from *vpn* → *frame*."""
        pte, _ = self.walk(vpn, allocate=True)
        assert pte is not None
        pte.present = True
        pte.frame_number = frame
        pte.swap_slot = None
        pte.read_write = read_write
        pte.user_supervisor = user_supervisor
        pte.no_execute = no_execute
        pte.accessed = False
        pte.dirty = False
        return pte

    def unmap_page(self, vpn: int) -> Optional[PageTableEntry]:
        """Remove a leaf mapping.  Returns the old PTE or ``None``."""
        pte, _ = self.walk(vpn, allocate=False)
        if pte is None:
            return None
        old_frame = pte.frame_number
        old_swap = pte.swap_slot
        pte.clear()
        # Return a snapshot of the old entry for the caller
        snapshot = PageTableEntry(
            present=False,
            frame_number=old_frame,
            swap_slot=old_swap,
        )
        return snapshot

    def mark_swapped(self, vpn: int, swap_slot: int) -> None:
        """Mark *vpn* as swapped out to *swap_slot*."""
        pte, _ = self.walk(vpn, allocate=False)
        if pte is not None:
            pte.present = False
            pte.frame_number = None
            pte.swap_slot = swap_slot
            pte.accessed = False
            pte.dirty = False

    def get_all_mapped_pages(self) -> List[Tuple[int, PageTableEntry]]:
        """Iterate all leaf PTEs that are present or swapped."""
        results: List[Tuple[int, PageTableEntry]] = []
        self._walk_level(self.root, [], results)
        return results

    def _walk_level(
        self,
        level: PageTableLevel,
        prefix_indices: List[int],
        results: List[Tuple[int, PageTableEntry]],
    ) -> None:
        is_leaf = level.level_index == self.config.levels - 1
        for idx, entry in enumerate(level.entries):
            current = prefix_indices + [idx]
            if is_leaf:
                if entry.present or entry.swap_slot is not None:
                    vpn = self.config.indices_to_virtual_page(current)
                    results.append((vpn, entry))
            else:
                if entry.next_level is not None:
                    self._walk_level(entry.next_level, current, results)

    @property
    def allocated_tables(self) -> int:
        return self._allocated_tables

    # ── Serialisation ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "root": self.root.to_dict(self.config),
            "allocated_tables": self._allocated_tables,
        }
