"""In-memory hardware simulator for capacity-aware dispatch decisions.

The API mirrors queue/throttle intent from AgentSkillOS execution throttling,
but uses deterministic ticks for local policy tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AdmissionStatus(str, Enum):
    """Admission decision for an incoming workload."""

    RUNNING = "running"
    QUEUED = "queued"
    REJECTED = "rejected"


@dataclass(frozen=True)
class HardwareProfile:
    """Static hardware limits used by the simulator."""

    total_capacity_units: int
    total_memory_mb: int
    max_concurrency: int
    max_queue_depth: int


@dataclass(frozen=True)
class WorkloadSpec:
    """Resource request for one execution dispatch."""

    workload_id: str
    capacity_units: int
    memory_mb: int
    duration_ticks: int


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Current simulator usage state."""

    used_capacity_units: int
    total_capacity_units: int
    used_memory_mb: int
    total_memory_mb: int
    running_count: int
    queued_count: int
    max_concurrency: int
    max_queue_depth: int

    @property
    def queue_utilization(self) -> float:
        if self.max_queue_depth <= 0:
            return 0.0
        return self.queued_count / self.max_queue_depth

    @property
    def memory_utilization(self) -> float:
        if self.total_memory_mb <= 0:
            return 0.0
        return self.used_memory_mb / self.total_memory_mb


@dataclass(frozen=True)
class AdmissionResult:
    """Outcome of a submit attempt."""

    status: AdmissionStatus
    snapshot: RuntimeSnapshot
    reason: str = ""
    queue_position: int | None = None


@dataclass
class _RunningWorkload:
    spec: WorkloadSpec
    remaining_ticks: int


class HardwareSimulator:
    """Simple queue + concurrency + memory/capacity simulator."""

    def __init__(self, profile: HardwareProfile):
        self.profile = profile
        self._running: dict[str, _RunningWorkload] = {}
        self._queue: list[WorkloadSpec] = []
        self._completed: list[str] = []

    def snapshot(self) -> RuntimeSnapshot:
        used_capacity = sum(item.spec.capacity_units for item in self._running.values())
        used_memory = sum(item.spec.memory_mb for item in self._running.values())
        return RuntimeSnapshot(
            used_capacity_units=used_capacity,
            total_capacity_units=self.profile.total_capacity_units,
            used_memory_mb=used_memory,
            total_memory_mb=self.profile.total_memory_mb,
            running_count=len(self._running),
            queued_count=len(self._queue),
            max_concurrency=self.profile.max_concurrency,
            max_queue_depth=self.profile.max_queue_depth,
        )

    def submit(self, spec: WorkloadSpec) -> AdmissionResult:
        """Try to run immediately, otherwise enqueue, otherwise reject."""
        if spec.capacity_units > self.profile.total_capacity_units:
            return AdmissionResult(
                status=AdmissionStatus.REJECTED,
                snapshot=self.snapshot(),
                reason="capacity_exceeds_profile",
            )
        if spec.memory_mb > self.profile.total_memory_mb:
            return AdmissionResult(
                status=AdmissionStatus.REJECTED,
                snapshot=self.snapshot(),
                reason="memory_exceeds_profile",
            )

        if self._can_start(spec):
            self._start(spec)
            return AdmissionResult(
                status=AdmissionStatus.RUNNING,
                snapshot=self.snapshot(),
            )

        if len(self._queue) >= self.profile.max_queue_depth:
            return AdmissionResult(
                status=AdmissionStatus.REJECTED,
                snapshot=self.snapshot(),
                reason="queue_full",
            )

        self._queue.append(spec)
        return AdmissionResult(
            status=AdmissionStatus.QUEUED,
            snapshot=self.snapshot(),
            queue_position=len(self._queue),
        )

    def tick(self, ticks: int = 1) -> list[str]:
        """Advance runtime clock and schedule queued work when slots free up."""
        if ticks <= 0:
            return []

        completed_now: list[str] = []
        for _ in range(ticks):
            for workload_id, running in list(self._running.items()):
                running.remaining_ticks -= 1
                if running.remaining_ticks <= 0:
                    del self._running[workload_id]
                    completed_now.append(workload_id)
                    self._completed.append(workload_id)
            self._drain_queue()
        return completed_now

    def completed_ids(self) -> tuple[str, ...]:
        return tuple(self._completed)

    def _can_start(self, spec: WorkloadSpec) -> bool:
        if len(self._running) >= self.profile.max_concurrency:
            return False

        snap = self.snapshot()
        next_capacity = snap.used_capacity_units + spec.capacity_units
        next_memory = snap.used_memory_mb + spec.memory_mb
        return (
            next_capacity <= self.profile.total_capacity_units
            and next_memory <= self.profile.total_memory_mb
        )

    def _start(self, spec: WorkloadSpec) -> None:
        self._running[spec.workload_id] = _RunningWorkload(
            spec=spec,
            remaining_ticks=max(spec.duration_ticks, 1),
        )

    def _drain_queue(self) -> None:
        while self._queue and self._can_start(self._queue[0]):
            queued = self._queue.pop(0)
            self._start(queued)


def _default_hardware_profile() -> HardwareProfile:
    return HardwareProfile(
        total_capacity_units=24,
        total_memory_mb=32_768,
        max_concurrency=3,
        max_queue_depth=8,
    )


_SHARED_SIMULATORS: dict[str, HardwareSimulator] = {}
_DEFAULT_MACHINE_KEY = "__default__"


def _normalized_machine_key(machine_id: str | None = None) -> str:
    value = (machine_id or "").strip()
    return value or _DEFAULT_MACHINE_KEY


def get_shared_hardware_simulator(machine_id: str | None = None) -> HardwareSimulator:
    """Process-wide simulator keyed by machine id so occupancy can be isolated per asset."""
    key = _normalized_machine_key(machine_id)
    simulator = _SHARED_SIMULATORS.get(key)
    if simulator is None:
        simulator = HardwareSimulator(_default_hardware_profile())
        _SHARED_SIMULATORS[key] = simulator
    return simulator


def reset_shared_hardware_simulator(machine_id: str | None = None) -> None:
    if machine_id is None:
        _SHARED_SIMULATORS.clear()
        return
    _SHARED_SIMULATORS.pop(_normalized_machine_key(machine_id), None)
