from app.runtime.hardware_simulator import (
    AdmissionStatus,
    HardwareProfile,
    HardwareSimulator,
    WorkloadSpec,
)


def test_hardware_simulator_respects_concurrency_and_queue_depth():
    simulator = HardwareSimulator(
        HardwareProfile(
            total_capacity_units=8,
            total_memory_mb=8_192,
            max_concurrency=1,
            max_queue_depth=1,
        )
    )

    running = simulator.submit(
        WorkloadSpec(
            workload_id="w1",
            capacity_units=3,
            memory_mb=1_024,
            duration_ticks=2,
        )
    )
    queued = simulator.submit(
        WorkloadSpec(
            workload_id="w2",
            capacity_units=3,
            memory_mb=1_024,
            duration_ticks=2,
        )
    )
    rejected = simulator.submit(
        WorkloadSpec(
            workload_id="w3",
            capacity_units=3,
            memory_mb=1_024,
            duration_ticks=2,
        )
    )

    assert running.status == AdmissionStatus.RUNNING
    assert queued.status == AdmissionStatus.QUEUED
    assert queued.queue_position == 1
    assert rejected.status == AdmissionStatus.REJECTED
    assert rejected.reason == "queue_full"

    completed = simulator.tick(ticks=2)
    assert completed == ["w1"]

    # After first workload completes, queued workload starts automatically.
    assert simulator.snapshot().running_count == 1
    assert simulator.snapshot().queued_count == 0


def test_hardware_simulator_rejects_memory_oversubscription():
    simulator = HardwareSimulator(
        HardwareProfile(
            total_capacity_units=10,
            total_memory_mb=2_048,
            max_concurrency=2,
            max_queue_depth=2,
        )
    )

    result = simulator.submit(
        WorkloadSpec(
            workload_id="too-big",
            capacity_units=2,
            memory_mb=4_096,
            duration_ticks=1,
        )
    )

    assert result.status == AdmissionStatus.REJECTED
    assert result.reason == "memory_exceeds_profile"

