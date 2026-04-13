"""
Global configuration for the Virtual Memory Simulator.

Uses a scaled-down address space so page tables and memory maps remain
visualizable.  The structural principles are identical to real x86-64
4-level paging — only the numeric widths are smaller.

All values here serve as *defaults*; they can be overridden at runtime
through the web UI configuration panel.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class SimConfig:
    """Mutable simulation configuration."""

    # ── Address geometry ────────────────────────────────────────────
    page_size: int = 4096              # 4 KB pages (2^12)
    page_offset_bits: int = 12         # log2(page_size)

    # 4-level page table, 2 bits per level → 256 virtual pages per process
    levels: int = 4
    bits_per_level: List[int] = field(default_factory=lambda: [2, 2, 2, 2])

    # ── Physical memory ────────────────────────────────────────────
    total_frames: int = 64             # total physical frames
    non_paged_frames: int = 8          # pinned frames (kernel / non-paged pool)

    # ── Swap ────────────────────────────────────────────────────────
    swap_slots: int = 128              # total swap-slot capacity

    # ── TLB ─────────────────────────────────────────────────────────
    tlb_size: int = 16                 # fully-associative TLB entries

    # ── Page replacement (WSClock) ──────────────────────────────────
    working_set_window: int = 10       # τ — virtual-time window size

    # ── Thrashing ───────────────────────────────────────────────────
    thrashing_threshold: float = 0.7   # page-fault-rate threshold (faults / access)
    thrashing_window: int = 20         # sliding window size for fault rate

    # ── Simulation ──────────────────────────────────────────────────
    swap_io_latency: int = 3           # ticks of simulated I/O latency

    # ── Derived helpers ─────────────────────────────────────────────
    @property
    def entries_per_level(self) -> List[int]:
        """Number of entries in each page-table level."""
        return [2 ** b for b in self.bits_per_level]

    @property
    def total_virtual_pages(self) -> int:
        """Total addressable virtual pages per process."""
        return 2 ** sum(self.bits_per_level)

    @property
    def virtual_address_bits(self) -> int:
        return sum(self.bits_per_level) + self.page_offset_bits

    @property
    def usable_frames(self) -> int:
        """Frames available for user-space paging."""
        return self.total_frames - self.non_paged_frames

    def virtual_page_to_indices(self, vpn: int) -> List[int]:
        """
        Decompose a virtual page number into per-level indices.

        Example (2-2-2-2): VPN 0b_10_11_00_01 → [2, 3, 0, 1]
        """
        indices = []
        for bits in reversed(self.bits_per_level):
            mask = (1 << bits) - 1
            indices.append(vpn & mask)
            vpn >>= bits
        indices.reverse()
        return indices

    def indices_to_virtual_page(self, indices: List[int]) -> int:
        """Reassemble per-level indices into a VPN."""
        vpn = 0
        for i, bits in enumerate(self.bits_per_level):
            vpn = (vpn << bits) | indices[i]
        return vpn

    def to_dict(self) -> dict:
        """Serialise for the REST API."""
        return {
            "page_size": self.page_size,
            "page_offset_bits": self.page_offset_bits,
            "levels": self.levels,
            "bits_per_level": self.bits_per_level,
            "total_frames": self.total_frames,
            "non_paged_frames": self.non_paged_frames,
            "usable_frames": self.usable_frames,
            "swap_slots": self.swap_slots,
            "tlb_size": self.tlb_size,
            "working_set_window": self.working_set_window,
            "thrashing_threshold": self.thrashing_threshold,
            "thrashing_window": self.thrashing_window,
            "swap_io_latency": self.swap_io_latency,
            "total_virtual_pages": self.total_virtual_pages,
            "virtual_address_bits": self.virtual_address_bits,
        }
