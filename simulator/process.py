"""
Process abstraction and process manager.

Each process owns a 4-level page table and a virtual address space
divided into named regions (code, heap, stack).  The process manager
handles creation, termination, suspension, and context switching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from simulator.config import SimConfig
from simulator.page_table import PageTable


class ProcessState(Enum):
    RUNNING = "running"
    READY = "ready"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


@dataclass
class MemoryRegion:
    """A contiguous region of the virtual address space."""
    name: str                   # e.g. "code", "heap", "stack"
    start_vpn: int
    end_vpn: int                # inclusive
    read_write: bool = True
    no_execute: bool = False

    @property
    def size(self) -> int:
        return self.end_vpn - self.start_vpn + 1

    def contains(self, vpn: int) -> bool:
        return self.start_vpn <= vpn <= self.end_vpn

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start_vpn": self.start_vpn,
            "end_vpn": self.end_vpn,
            "size": self.size,
            "read_write": self.read_write,
            "no_execute": self.no_execute,
        }


class Process:
    """A single simulated process."""

    _next_pid: int = 1

    def __init__(self, name: str, config: SimConfig, regions: List[MemoryRegion]):
        self.pid: int = Process._next_pid
        Process._next_pid += 1

        self.name = name
        self.config = config
        self.state = ProcessState.READY
        self.page_table = PageTable(config)
        self.regions = regions
        self.cr3 = self.pid  # simulated CR3 value (just the PID as identifier)

        # Working set tracking
        self.working_set: set[int] = set()
        self.working_set_history: List[int] = []  # size over time

        # Stats
        self.page_faults: int = 0
        self.memory_accesses: int = 0
        self.resident_pages: int = 0

    def is_valid_vpn(self, vpn: int) -> bool:
        """Check if *vpn* falls within any mapped region."""
        return any(r.contains(vpn) for r in self.regions)

    def get_region_for_vpn(self, vpn: int) -> Optional[MemoryRegion]:
        for r in self.regions:
            if r.contains(vpn):
                return r
        return None

    def record_access(self, vpn: int, faulted: bool) -> None:
        self.memory_accesses += 1
        if faulted:
            self.page_faults += 1
        self.working_set.add(vpn)

    @property
    def fault_rate(self) -> float:
        if self.memory_accesses == 0:
            return 0.0
        return self.page_faults / self.memory_accesses

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "state": self.state.value,
            "cr3": self.cr3,
            "regions": [r.to_dict() for r in self.regions],
            "working_set_size": len(self.working_set),
            "working_set": sorted(self.working_set),
            "page_faults": self.page_faults,
            "memory_accesses": self.memory_accesses,
            "fault_rate": round(self.fault_rate, 4),
            "resident_pages": self.resident_pages,
            "page_table": self.page_table.to_dict(),
        }


class ProcessManager:
    """Creates, tracks, and manages simulated processes."""

    def __init__(self, config: SimConfig):
        self.config = config
        self.processes: Dict[int, Process] = {}
        self.active_pid: Optional[int] = None

    def create_process(
        self,
        name: str,
        regions: Optional[List[MemoryRegion]] = None,
    ) -> Process:
        """Create a new process with the given memory regions."""
        if regions is None:
            # Default region layout: code + heap + stack
            total = self.config.total_virtual_pages
            code_end = total // 4 - 1
            heap_start = total // 4
            heap_end = total * 3 // 4 - 1
            stack_start = total * 3 // 4
            stack_end = total - 1
            regions = [
                MemoryRegion("code", 0, code_end, read_write=False, no_execute=False),
                MemoryRegion("heap", heap_start, heap_end, read_write=True, no_execute=True),
                MemoryRegion("stack", stack_start, stack_end, read_write=True, no_execute=True),
            ]

        proc = Process(name, self.config, regions)
        self.processes[proc.pid] = proc

        if self.active_pid is None:
            proc.state = ProcessState.RUNNING
            self.active_pid = proc.pid

        return proc

    def terminate_process(self, pid: int) -> Optional[Process]:
        proc = self.processes.get(pid)
        if proc is None:
            return None
        proc.state = ProcessState.TERMINATED
        if self.active_pid == pid:
            self.active_pid = None
            # Activate the next ready process
            for p in self.processes.values():
                if p.state == ProcessState.READY:
                    p.state = ProcessState.RUNNING
                    self.active_pid = p.pid
                    break
        return proc

    def suspend_process(self, pid: int) -> Optional[Process]:
        """Suspend process — its working set will be paged out."""
        proc = self.processes.get(pid)
        if proc is None or proc.state == ProcessState.TERMINATED:
            return None
        proc.state = ProcessState.SUSPENDED
        if self.active_pid == pid:
            self.active_pid = None
            for p in self.processes.values():
                if p.state == ProcessState.READY:
                    p.state = ProcessState.RUNNING
                    self.active_pid = p.pid
                    break
        return proc

    def resume_process(self, pid: int) -> Optional[Process]:
        proc = self.processes.get(pid)
        if proc is None or proc.state != ProcessState.SUSPENDED:
            return None
        proc.state = ProcessState.READY
        if self.active_pid is None:
            proc.state = ProcessState.RUNNING
            self.active_pid = pid
        return proc

    def get(self, pid: int) -> Optional[Process]:
        return self.processes.get(pid)

    def get_active(self) -> Optional[Process]:
        if self.active_pid is not None:
            return self.processes.get(self.active_pid)
        return None

    @property
    def runnable_processes(self) -> List[Process]:
        return [
            p for p in self.processes.values()
            if p.state in (ProcessState.RUNNING, ProcessState.READY)
        ]

    def to_dict(self) -> dict:
        return {
            "active_pid": self.active_pid,
            "processes": {
                pid: p.to_dict() for pid, p in self.processes.items()
                if p.state != ProcessState.TERMINATED
            },
        }
