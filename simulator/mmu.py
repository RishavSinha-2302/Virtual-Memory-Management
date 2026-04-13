"""
Memory Management Unit (MMU).

Orchestrates address translation: TLB → 4-level page walk → page fault
handling.  Coordinates with the frame table, swap manager, and page
replacement algorithm.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional, Tuple, List

if TYPE_CHECKING:
    from simulator.process import Process

from simulator.config import SimConfig
from simulator.tlb import TLB
from simulator.page_table import PageTable
from simulator.frame_table import FrameTable, FrameState
from simulator.swap_manager import SwapManager
from simulator.page_replacement import WSClockReplacer
from simulator.statistics import SimulatorStats


class AccessType(Enum):
    READ = "read"
    WRITE = "write"


class PageFaultType(Enum):
    DEMAND_ZERO = "demand_zero"     # first access — allocate + zero
    SWAP_IN = "swap_in"             # page is in swap — read it back


class MMU:
    """
    Simulated Memory Management Unit.

    Translates virtual addresses to physical addresses using the TLB
    and multi-level page table.  Handles page faults by demand-zeroing
    or swapping in, invoking the page replacement algorithm when
    physical memory is exhausted.
    """

    def __init__(
        self,
        config: SimConfig,
        tlb: TLB,
        frame_table: FrameTable,
        swap_manager: SwapManager,
        replacer: WSClockReplacer,
        stats: SimulatorStats,
    ):
        self.config = config
        self.tlb = tlb
        self.frame_table = frame_table
        self.swap_manager = swap_manager
        self.replacer = replacer
        self.stats = stats

        self.current_time: int = 0
        self.event_log: List[dict] = []

    # ── Public API ──────────────────────────────────────────────────
    def translate(
        self,
        process: "Process",  # type hint only — avoids circular import
        vpn: int,
        access_type: AccessType,
    ) -> Tuple[int, bool, List[dict]]:
        """
        Translate *vpn* for *process*.

        Returns ``(frame_number, page_faulted, events)`` where *events*
        is a list of log entries describing what happened.
        """
        events: List[dict] = []
        pid = process.pid
        page_faulted = False
        tlb_hit = False

        # ── Step 1: TLB lookup ──────────────────────────────────────
        tlb_entry = self.tlb.lookup(pid, vpn)
        if tlb_entry is not None:
            tlb_hit = True
            frame = tlb_entry.frame_number
            events.append(self._event("TLB_HIT", pid, vpn, frame=frame))

            # Update PTE and frame bits
            ft_entry = self.frame_table.get(frame)
            ft_entry.accessed = True
            ft_entry.last_access_time = self.current_time
            if access_type == AccessType.WRITE:
                tlb_entry.dirty = True
                ft_entry.dirty = True
                # Also update the PTE dirty flag
                pte, _ = process.page_table.walk(vpn)
                if pte:
                    pte.dirty = True
                    pte.accessed = True
        else:
            events.append(self._event("TLB_MISS", pid, vpn))

            # ── Step 2: Page table walk ─────────────────────────────
            pte, indices = process.page_table.walk(vpn, allocate=False)
            events.append(self._event("PAGE_WALK", pid, vpn,
                                       detail=f"Indices: {indices}"))

            if pte is not None and pte.present:
                # PTE hit — page is resident
                frame = pte.frame_number
                events.append(self._event("PTE_HIT", pid, vpn, frame=frame))

                pte.accessed = True
                if access_type == AccessType.WRITE:
                    pte.dirty = True

                ft_entry = self.frame_table.get(frame)
                ft_entry.accessed = True
                ft_entry.last_access_time = self.current_time
                if access_type == AccessType.WRITE:
                    ft_entry.dirty = True

                # Insert into TLB
                self.tlb.insert(pid, vpn, frame, dirty=pte.dirty)
            else:
                # ── Step 3: PAGE FAULT ──────────────────────────────
                page_faulted = True
                frame, fault_events = self._handle_page_fault(
                    process, vpn, access_type, pte
                )
                events.extend(fault_events)

                if access_type == AccessType.WRITE:
                    pte_after, _ = process.page_table.walk(vpn)
                    if pte_after:
                        pte_after.dirty = True
                    ft_entry = self.frame_table.get(frame)
                    ft_entry.dirty = True
                    self.tlb.insert(pid, vpn, frame, dirty=True)
                else:
                    self.tlb.insert(pid, vpn, frame, dirty=False)

        # Record stats
        self.stats.record_access(pid, vpn, access_type.value, tlb_hit, page_faulted)
        process.record_access(vpn, page_faulted)

        self.event_log.extend(events)
        return (frame, page_faulted, events)

    # ── Page fault handler ──────────────────────────────────────────
    def _handle_page_fault(
        self,
        process: "Process",
        vpn: int,
        access_type: AccessType,
        pte,  # Optional[PageTableEntry]
    ) -> Tuple[int, List[dict]]:
        """
        Handle a page fault for *vpn* in *process*.

        1. Determine fault type (demand-zero or swap-in).
        2. Acquire a free frame (evicting if necessary).
        3. Load the page into the frame.
        4. Update PTE.

        Returns ``(frame_number, events)``.
        """
        events: List[dict] = []
        pid = process.pid

        # Determine fault type
        swap_slot = None
        if pte is not None and pte.swap_slot is not None:
            fault_type = PageFaultType.SWAP_IN
            swap_slot = pte.swap_slot
            events.append(self._event(
                "PAGE_FAULT", pid, vpn,
                detail=f"Type: SWAP_IN from slot {swap_slot}",
            ))
        else:
            fault_type = PageFaultType.DEMAND_ZERO
            events.append(self._event(
                "PAGE_FAULT", pid, vpn,
                detail="Type: DEMAND_ZERO (first access)",
            ))

        # Acquire a frame
        frame = self.frame_table.allocate_frame(
            pid, vpn, label=f"P{pid}:VP{vpn}"
        )

        if frame is None:
            # No free frames — must evict
            evict_events = self._evict_page()
            events.extend(evict_events)
            # Retry allocation
            frame = self.frame_table.allocate_frame(
                pid, vpn, label=f"P{pid}:VP{vpn}"
            )
            if frame is None:
                raise RuntimeError("Failed to allocate frame after eviction")

        events.append(self._event(
            "FRAME_ALLOC", pid, vpn, frame=frame,
            detail=f"Allocated frame {frame}",
        ))

        # Load page content
        if fault_type == PageFaultType.SWAP_IN and swap_slot is not None:
            label = self.swap_manager.read_page(swap_slot)
            self.stats.record_swap_read()
            self.swap_manager.free_slot(swap_slot)
            ft = self.frame_table.get(frame)
            ft.label = label
            events.append(self._event(
                "SWAP_IN", pid, vpn, frame=frame,
                detail=f"Read from swap slot {swap_slot}",
            ))
        else:
            # Demand zero — label only
            ft = self.frame_table.get(frame)
            ft.label = f"P{pid}:VP{vpn}"

        # Update frame metadata
        ft = self.frame_table.get(frame)
        ft.accessed = True
        ft.last_access_time = self.current_time

        # Update page table
        region = process.get_region_for_vpn(vpn)
        rw = region.read_write if region else True
        nx = region.no_execute if region else False
        process.page_table.map_page(vpn, frame, read_write=rw, no_execute=nx)

        # Add to clock ring
        self.replacer.add_frame(frame)

        # Update resident count
        process.resident_pages = len(
            self.frame_table.get_frames_for_process(pid)
        )

        return (frame, events)

    def _evict_page(self) -> List[dict]:
        """
        Evict one page using the WSClock algorithm.

        Returns the log events produced during eviction.
        """
        events: List[dict] = []

        result = self.replacer.select_victim(self.current_time)
        if result is None:
            raise RuntimeError("WSClock failed to find a victim")

        victim_frame_id, was_dirty = result
        victim = self.frame_table.get(victim_frame_id)
        v_pid = victim.owning_pid
        v_vpn = victim.virtual_page

        events.append(self._event(
            "EVICT", v_pid, v_vpn, frame=victim_frame_id,
            detail=f"{'Dirty' if was_dirty else 'Clean'} page evicted",
        ))

        # If dirty, write to swap before freeing
        if was_dirty:
            slot = self.swap_manager.allocate_slot(v_pid, v_vpn)
            if slot is None:
                raise RuntimeError("Swap space exhausted")
            self.swap_manager.write_page(slot, victim.label)
            self.stats.record_swap_write()
            events.append(self._event(
                "SWAP_OUT", v_pid, v_vpn, frame=victim_frame_id,
                detail=f"Written to swap slot {slot}",
            ))

            # Update victim's PTE to point to swap
            if v_pid is not None and v_vpn is not None:
                from simulator.process import Process  # deferred import
                # We'll need the process manager to get the process
                # This is handled via the engine; here we use the trick of
                # looking up via the _processes_ref set by the engine
                proc = self._find_process(v_pid)
                if proc is not None:
                    proc.page_table.mark_swapped(v_vpn, slot)
                    self.tlb.invalidate(v_pid, v_vpn)
        else:
            # Clean page — just discard
            if v_pid is not None and v_vpn is not None:
                proc = self._find_process(v_pid)
                if proc is not None:
                    # Mark as swapped with no slot (will demand-zero on re-access)
                    # Actually for clean pages we should still track them in swap
                    # if they came from swap; otherwise they're demand-zero.
                    # For simplicity: clean eviction = data lost, will demand-zero.
                    pte, _ = proc.page_table.walk(v_vpn)
                    if pte is not None:
                        pte.present = False
                        pte.frame_number = None
                        # Keep swap_slot as None — page will demand-zero
                    self.tlb.invalidate(v_pid, v_vpn)

        # Remove from clock ring and free frame
        self.replacer.remove_frame(victim_frame_id)
        self.frame_table.free_frame(victim_frame_id)
        self.stats.record_eviction()

        return events

    # ── Process lookup (set by engine) ──────────────────────────────
    _process_lookup = None  # type: ignore

    def set_process_lookup(self, lookup_fn) -> None:
        """Called by the engine to provide a process lookup function."""
        self._process_lookup = lookup_fn

    def _find_process(self, pid: int):
        if self._process_lookup:
            return self._process_lookup(pid)
        return None

    # ── Helpers ─────────────────────────────────────────────────────
    def _event(
        self,
        event_type: str,
        pid: Optional[int],
        vpn: Optional[int],
        frame: Optional[int] = None,
        detail: str = "",
    ) -> dict:
        return {
            "time": self.current_time,
            "type": event_type,
            "pid": pid,
            "vpn": vpn,
            "frame": frame,
            "detail": detail,
        }

    def get_recent_events(self, n: int = 50) -> List[dict]:
        return self.event_log[-n:]

    def to_dict(self) -> dict:
        return {
            "current_time": self.current_time,
            "total_events": len(self.event_log),
            "recent_events": self.get_recent_events(200),
        }
