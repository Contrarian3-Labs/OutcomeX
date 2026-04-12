from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import Settings, get_settings
from app.execution.contracts import ExecutionStrategy
from app.integrations.agentskillos_bridge import resolve_agentskillos_repo_root


@dataclass(frozen=True)
class CuratedBenchmarkSolutionSpec:
    task_id: str
    title: str
    summary: str
    best_fit: str
    estimated_minutes: int
    price_range: str
    featured: bool = False
    preferred_native_plan_index: int | None = None


@dataclass(frozen=True)
class BenchmarkSolution:
    id: str
    title: str
    summary: str
    category: str
    benchmark_task_name: str
    benchmark_description: str
    benchmark_prompt: str
    best_fit: str
    estimated_minutes: int
    price_range: str
    outputs: tuple[str, ...]
    skills: tuple[str, ...]
    input_files: tuple[str, ...]
    featured: bool = False
    preferred_native_plan_index: int | None = None

    @property
    def preferred_execution_strategy(self) -> ExecutionStrategy | None:
        if self.preferred_native_plan_index == 0:
            return ExecutionStrategy.QUALITY
        if self.preferred_native_plan_index == 1:
            return ExecutionStrategy.EFFICIENCY
        if self.preferred_native_plan_index == 2:
            return ExecutionStrategy.SIMPLICITY
        return None


CURATED_SOLUTION_SPECS: tuple[CuratedBenchmarkSolutionSpec, ...] = (
    CuratedBenchmarkSolutionSpec(
        task_id="visual_creation_task3",
        title="Premium Launch Visual Pack",
        summary="Create a polished multi-asset social pack for a brand launch, including hero visuals and a preview grid.",
        best_fit="Launch announcements, product drops, campaign visuals",
        estimated_minutes=14,
        price_range="18-42 PWR",
        featured=True,
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="visual_creation_task2",
        title="Product UX Visual Research Pack",
        summary="Generate a competitor-style visual research pack plus fusion concepts for product and brand direction.",
        best_fit="Product positioning, concept exploration, visual benchmarking",
        estimated_minutes=18,
        price_range="22-48 PWR",
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="visual_creation_task5",
        title="Immersive Exhibition Site Pack",
        summary="Build a media-rich promotional site with generated key art, thumbnails, and a branded showcase page.",
        best_fit="Campaign microsites, exhibition promos, immersive showcases",
        estimated_minutes=26,
        price_range="35-65 PWR",
        featured=True,
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="visual_creation_task6",
        title="Illustrated Scroll Story",
        summary="Produce a long-form visual narrative with generated chapter art and a scroll-driven web experience.",
        best_fit="Editorial stories, narrative explainers, immersive branded content",
        estimated_minutes=24,
        price_range="30-58 PWR",
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="motion_video_task1",
        title="Educational Motion Explainer",
        summary="Create a polished educational animation that turns an abstract concept into a teachable visual sequence.",
        best_fit="Teaching assets, technical explainers, concept storytelling",
        estimated_minutes=20,
        price_range="24-52 PWR",
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="motion_video_task4",
        title="Concept Explainer Video",
        summary="Generate a short animated explainer video for a complex system or model concept.",
        best_fit="Product explainers, AI concepts, motion-first education",
        estimated_minutes=22,
        price_range="28-56 PWR",
        featured=True,
        preferred_native_plan_index=2,
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="document_creation_task1",
        title="Technical Service Contract Kit",
        summary="Draft a structured service contract with formal sections, fee tables, and revision markup.",
        best_fit="Ops, vendor agreements, consulting engagements",
        estimated_minutes=12,
        price_range="14-28 PWR",
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="document_creation_task5",
        title="Quarterly Performance Deck",
        summary="Generate a presentation-ready quarterly business report with charts, tables, and consistent structure.",
        best_fit="Investor updates, board prep, business reviews",
        estimated_minutes=16,
        price_range="18-36 PWR",
        featured=True,
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="data_computation_task2",
        title="Portfolio Risk Simulation Workbook",
        summary="Run a Monte Carlo-style portfolio VaR workflow and package the results into structured outputs.",
        best_fit="Risk analysis, finance ops, portfolio reporting",
        estimated_minutes=16,
        price_range="16-34 PWR",
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="data_computation_task6",
        title="Quantitative Visualization Pack",
        summary="Compute multiple analytical results and package them into structured data plus publication-style visual outputs.",
        best_fit="Technical analysis, quantitative explainers, research visuals",
        estimated_minutes=15,
        price_range="16-34 PWR",
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="web_interaction_task1",
        title="Competitor Website Analysis Report",
        summary="Research live competitor websites, capture key pages, and package the findings into a structured report.",
        best_fit="Product strategy, market research, positioning review",
        estimated_minutes=18,
        price_range="20-40 PWR",
        preferred_native_plan_index=2,
    ),
    CuratedBenchmarkSolutionSpec(
        task_id="web_interaction_task2",
        title="AI Weekly Web Brief",
        summary="Curate live AI stories from the web, capture source evidence, and turn them into a formatted newsletter.",
        best_fit="Founder updates, research recaps, content operations",
        estimated_minutes=17,
        price_range="18-38 PWR",
        preferred_native_plan_index=2,
    ),
)


def _agentskillos_root(settings: Settings) -> Path:
    configured_root = (settings.agentskillos_root or "").strip()
    if configured_root:
        path = Path(configured_root).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"agentskillos_root_missing:{path}")
        return path

    resolved_root = resolve_agentskillos_repo_root(settings)
    if resolved_root is None:
        raise RuntimeError("agentskillos_root_not_configured")
    return resolved_root.resolve()


def _task_config_path(task_id: str, *, settings: Settings) -> Path:
    return _agentskillos_root(settings) / "benchmark" / "AgentSkillOS_bench" / "tasks" / f"{task_id}.json"


def _task_data_dir(task_id: str, *, settings: Settings) -> Path:
    return _agentskillos_root(settings) / "benchmark" / "AgentSkillOS_bench" / "task_data" / task_id


@lru_cache(maxsize=1)
def _catalog_cache_key() -> str:
    settings = get_settings()
    resolved_root = resolve_agentskillos_repo_root(settings)
    if resolved_root is not None:
        return str(resolved_root.resolve())
    return (settings.agentskillos_root or "").strip()


def reset_benchmark_solution_cache() -> None:
    _catalog_cache_key.cache_clear()
    _load_catalog_cached.cache_clear()


@lru_cache(maxsize=1)
def _load_catalog_cached(_root_key: str) -> tuple[BenchmarkSolution, ...]:
    settings = get_settings()
    items: list[BenchmarkSolution] = []
    for spec in CURATED_SOLUTION_SPECS:
        config_path = _task_config_path(spec.task_id, settings=settings)
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        task_data_dir = _task_data_dir(spec.task_id, settings=settings)
        input_files = ()
        if task_data_dir.exists():
            input_files = tuple(
                sorted(path.relative_to(task_data_dir).as_posix() for path in task_data_dir.rglob("*") if path.is_file())
            )

        items.append(
            BenchmarkSolution(
                id=spec.task_id,
                title=spec.title,
                summary=spec.summary,
                category=str(payload.get("category", "")),
                benchmark_task_name=str(payload.get("task_name", spec.task_id)),
                benchmark_description=str(payload.get("description", "")),
                benchmark_prompt=str(payload.get("prompt", "")),
                best_fit=spec.best_fit,
                estimated_minutes=spec.estimated_minutes,
                price_range=spec.price_range,
                outputs=tuple(str(item) for item in payload.get("outputs", [])),
                skills=tuple(str(item) for item in payload.get("skills", [])),
                input_files=input_files,
                featured=spec.featured,
                preferred_native_plan_index=spec.preferred_native_plan_index,
            )
        )
    return tuple(items)


def list_benchmark_solutions() -> tuple[BenchmarkSolution, ...]:
    return _load_catalog_cached(_catalog_cache_key())


def get_benchmark_solution(solution_id: str) -> BenchmarkSolution | None:
    for item in list_benchmark_solutions():
        if item.id == solution_id:
            return item
    return None
