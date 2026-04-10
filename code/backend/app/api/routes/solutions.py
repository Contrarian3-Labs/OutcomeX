from fastapi import APIRouter, HTTPException, status

from app.domain.benchmark_solutions import get_benchmark_solution, list_benchmark_solutions
from app.schemas.solution import SolutionResponse

router = APIRouter()


@router.get("", response_model=list[SolutionResponse])
def list_solutions() -> list[SolutionResponse]:
    return [
        SolutionResponse(
            id=item.id,
            title=item.title,
            summary=item.summary,
            category=item.category,
            benchmark_task_name=item.benchmark_task_name,
            benchmark_description=item.benchmark_description,
            benchmark_prompt=item.benchmark_prompt,
            best_fit=item.best_fit,
            estimated_minutes=item.estimated_minutes,
            price_range=item.price_range,
            outputs=list(item.outputs),
            skills=list(item.skills),
            input_files=list(item.input_files),
            featured=item.featured,
        )
        for item in list_benchmark_solutions()
    ]


@router.get("/{solution_id}", response_model=SolutionResponse)
def get_solution(solution_id: str) -> SolutionResponse:
    item = get_benchmark_solution(solution_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return SolutionResponse(
        id=item.id,
        title=item.title,
        summary=item.summary,
        category=item.category,
        benchmark_task_name=item.benchmark_task_name,
        benchmark_description=item.benchmark_description,
        benchmark_prompt=item.benchmark_prompt,
        best_fit=item.best_fit,
        estimated_minutes=item.estimated_minutes,
        price_range=item.price_range,
        outputs=list(item.outputs),
        skills=list(item.skills),
        input_files=list(item.input_files),
        featured=item.featured,
    )
