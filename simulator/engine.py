"""
Simulation Engine — the top-level orchestrator.

Owns all subsystems and provides the public API consumed by
the Flask application.  Supports step-by-step execution,
scenario loading, and full state introspection.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from simulator.config import SimConfig
from simulator.page_table import PageTable
from simulator.frame_table import FrameTable
from simulator.tlb import TLB
from simulator.swap_manager import SwapManager
from simulator.page_replacement import WSClockReplacer
from simulator.process import ProcessManager, Process, MemoryRegion, ProcessState
from simulator.mmu import MMU, AccessType
from simulator.thrashing_detector import ThrashingDetector
from simulator.statistics import SimulatorStats


class SimulationEngine:
    """
    Top-level simulation controller.

    Usage::

        engine = SimulationEngine()
        engine.create_process("App1")
        events = engine.execute_access(pid=1, vpn=5, access_type="read")
        state  = engine.get_full_state()
    """

    def __init__(self, config: Optional[SimConfig] = None):
        self.config = config or SimConfig()
        self._init_subsystems()

        # Simulation state
        self.time: int = 0
        self.running: bool = False
        self.event_log: List[dict] = []

        # Scenario auto-run state
        self._scenario_queue: List[dict] = []
        self._scenario_name: str = ""

    def _init_subsystems(self) -> None:
        """(Re)initialise every subsystem from scratch."""
        self.frame_table = FrameTable(self.config)
        self.tlb = TLB(self.config)
        self.swap_manager = SwapManager(self.config)
        self.stats = SimulatorStats(self.config)
        self.replacer = WSClockReplacer(self.config, self.frame_table)
        self.process_mgr = ProcessManager(self.config)
        self.thrashing = ThrashingDetector(self.config)

        self.mmu = MMU(
            self.config, self.tlb, self.frame_table,
            self.swap_manager, self.replacer, self.stats,
        )
        # Give the MMU a way to look up processes
        self.mmu.set_process_lookup(self.process_mgr.get)

        # Reset process PID counter
        Process._next_pid = 1

    # ── Simulation control ──────────────────────────────────────────
    def reset(self, config: Optional[SimConfig] = None) -> None:
        """Full reset with optional new config."""
        if config is not None:
            self.config = config
        self._init_subsystems()
        self.time = 0
        self.running = False
        self.event_log.clear()
        self._scenario_queue.clear()
        self._scenario_name = ""

    def step(self) -> List[dict]:
        """
        Advance simulation by one tick.

        If a scenario is loaded, execute the next queued access.
        Returns the events produced.
        """
        if self._scenario_queue:
            action = self._scenario_queue.pop(0)
            return self._execute_action(action)
        return []

    def execute_access(
        self, pid: int, vpn: int, access_type: str = "read"
    ) -> List[dict]:
        """Manually trigger a single memory access."""
        return self._execute_action({
            "pid": pid, "vpn": vpn, "type": access_type,
        })

    def _execute_action(self, action: dict) -> List[dict]:
        """Internal: perform one memory access and advance time."""
        pid = action["pid"]
        vpn = action["vpn"]
        atype = AccessType.READ if action.get("type", "read") == "read" else AccessType.WRITE

        proc = self.process_mgr.get(pid)
        if proc is None or proc.state == ProcessState.TERMINATED:
            event = {
                "time": self.time, "type": "ERROR", "pid": pid, "vpn": vpn,
                "frame": None, "detail": f"Process {pid} not found or terminated",
            }
            self.event_log.append(event)
            return [event]

        if proc.state == ProcessState.SUSPENDED:
            event = {
                "time": self.time, "type": "ERROR", "pid": pid, "vpn": vpn,
                "frame": None, "detail": f"Process {pid} is suspended",
            }
            self.event_log.append(event)
            return [event]

        if not proc.is_valid_vpn(vpn):
            event = {
                "time": self.time, "type": "SEGFAULT", "pid": pid, "vpn": vpn,
                "frame": None, "detail": f"VPN {vpn} outside mapped regions",
            }
            self.event_log.append(event)
            return [event]

        # Context switch if needed
        ctx_events: List[dict] = []
        if self.process_mgr.active_pid != pid:
            ctx_events = self._context_switch(pid)

        self.mmu.current_time = self.time
        frame, faulted, events = self.mmu.translate(proc, vpn, atype)

        # Record in thrashing detector
        self.thrashing.record_access(self.time, faulted)

        # Take stats snapshot every 5 ticks
        if self.time % 5 == 0:
            self.stats.take_snapshot(self.time, extra={
                "free_frames": self.frame_table.free_count,
                "thrashing": self.thrashing.is_thrashing,
            })

        all_events = ctx_events + events
        self.event_log.extend(all_events)
        self.time += 1
        return all_events

    def _context_switch(self, to_pid: int) -> List[dict]:
        """Perform a context switch to *to_pid*."""
        old_pid = self.process_mgr.active_pid
        events: List[dict] = []

        if old_pid is not None:
            old_proc = self.process_mgr.get(old_pid)
            if old_proc and old_proc.state == ProcessState.RUNNING:
                old_proc.state = ProcessState.READY

        new_proc = self.process_mgr.get(to_pid)
        if new_proc:
            new_proc.state = ProcessState.RUNNING
            self.process_mgr.active_pid = to_pid

        # Flush TLB entries for old process
        flushed = self.tlb.flush(old_pid) if old_pid else 0
        self.stats.record_context_switch()

        events.append({
            "time": self.time, "type": "CONTEXT_SWITCH",
            "pid": to_pid, "vpn": None, "frame": None,
            "detail": f"Switch from PID {old_pid} → {to_pid}, TLB flushed {flushed} entries",
        })
        return events

    # ── Process management (exposed to API) ─────────────────────────
    def create_process(
        self, name: str, regions: Optional[List[dict]] = None
    ) -> dict:
        """Create a process.  *regions* is a list of region dicts."""
        mem_regions = None
        if regions:
            mem_regions = [
                MemoryRegion(
                    name=r["name"],
                    start_vpn=r["start_vpn"],
                    end_vpn=r["end_vpn"],
                    read_write=r.get("read_write", True),
                    no_execute=r.get("no_execute", False),
                )
                for r in regions
            ]
        proc = self.process_mgr.create_process(name, mem_regions)
        return proc.to_dict()

    def suspend_process(self, pid: int) -> List[dict]:
        """Suspend a process and page out its working set."""
        events: List[dict] = []
        proc = self.process_mgr.suspend_process(pid)
        if proc is None:
            return [{"time": self.time, "type": "ERROR", "detail": f"Cannot suspend PID {pid}"}]

        events.append({
            "time": self.time, "type": "SUSPEND",
            "pid": pid, "vpn": None, "frame": None,
            "detail": f"Process {proc.name} suspended",
        })

        # Page out all resident pages
        frames = self.frame_table.get_frames_for_process(pid)
        for f in frames:
            vpn = f.virtual_page
            if f.dirty:
                slot = self.swap_manager.allocate_slot(pid, vpn)
                if slot is not None:
                    self.swap_manager.write_page(slot, f.label)
                    self.stats.record_swap_write()
                    proc.page_table.mark_swapped(vpn, slot)
                    events.append({
                        "time": self.time, "type": "SWAP_OUT",
                        "pid": pid, "vpn": vpn, "frame": f.frame_id,
                        "detail": f"Dirty page written to swap slot {slot}",
                    })
            else:
                # Clean — just drop
                pte, _ = proc.page_table.walk(vpn)
                if pte:
                    pte.present = False
                    pte.frame_number = None

            self.replacer.remove_frame(f.frame_id)
            self.tlb.invalidate(pid, vpn)
            self.frame_table.free_frame(f.frame_id)

        proc.resident_pages = 0
        proc.working_set.clear()
        self.event_log.extend(events)
        return events

    def resume_process(self, pid: int) -> List[dict]:
        proc = self.process_mgr.resume_process(pid)
        if proc is None:
            return [{"time": self.time, "type": "ERROR", "detail": f"Cannot resume PID {pid}"}]
        event = {
            "time": self.time, "type": "RESUME",
            "pid": pid, "vpn": None, "frame": None,
            "detail": f"Process {proc.name} resumed",
        }
        self.event_log.append(event)
        return [event]

    # ── Scenario management ─────────────────────────────────────────
    def load_scenario(self, scenario: dict) -> str:
        """
        Load a scenario dict.  Expected format::

            {
              "name": "...",
              "config": { ... },           # optional overrides
              "processes": [ { "name": "...", "regions": [...] } ],
              "accesses": [ { "pid": 1, "vpn": 5, "type": "read" }, ... ]
            }
        """
        self.reset()
        self._scenario_name = scenario.get("name", "Unnamed")

        # Apply config overrides
        if "config" in scenario:
            for k, v in scenario["config"].items():
                if hasattr(self.config, k):
                    setattr(self.config, k, v)
            self._init_subsystems()

        # Create processes
        for pdef in scenario.get("processes", []):
            self.create_process(pdef["name"], pdef.get("regions"))

        # Queue accesses
        self._scenario_queue = list(scenario.get("accesses", []))
        return self._scenario_name

    def load_scenario_file(self, path: str) -> str:
        with open(path, "r") as f:
            data = json.load(f)
        return self.load_scenario(data)

    @property
    def scenario_remaining(self) -> int:
        return len(self._scenario_queue)

    # ── Full state snapshot ─────────────────────────────────────────
    def get_full_state(self) -> dict:
        ws_sizes = {}
        for pid, proc in self.process_mgr.processes.items():
            if proc.state != ProcessState.TERMINATED:
                ws_sizes[pid] = len(proc.working_set)

        return {
            "time": self.time,
            "config": self.config.to_dict(),
            "processes": self.process_mgr.to_dict(),
            "frame_table": self.frame_table.to_dict(),
            "tlb": self.tlb.to_dict(),
            "swap": self.swap_manager.to_dict(),
            "replacer": self.replacer.to_dict(),
            "stats": self.stats.to_dict(),
            "thrashing": self.thrashing.to_dict(),
            "mmu": self.mmu.to_dict(),
            "scenario_name": self._scenario_name,
            "scenario_remaining": self.scenario_remaining,
            "recommendation": self.thrashing.get_recommendation(
                ws_sizes, self.frame_table.free_count,
            ),
        }

    def get_event_log(self, last_n: int = 100) -> List[dict]:
        return self.event_log[-last_n:]

    def get_timeline(self) -> List[dict]:
        return self.stats.get_timeline()
