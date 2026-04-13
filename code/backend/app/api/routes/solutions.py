from fastapi import APIRouter, HTTPException, status

from app.domain.benchmark_solutions import get_benchmark_solution, list_benchmark_solutions
from app.runtime.cost_service import get_runtime_cost_service
from app.schemas.solution import SolutionResponse

router = APIRouter()


@router.get("", response_model=list[SolutionResponse])
def list_solutions() -> list[SolutionResponse]:
    cost_service = get_runtime_cost_service()
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
            quoted_amount_cents=(quote := cost_service.quote_for_prompt(item.benchmark_prompt)).official_quote_cents,
            quoted_pwr_amount=quote.pwr_quote,
            quoted_pwr_anchor_price_cents=quote.pwr_anchor_price_cents,
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
    quote = get_runtime_cost_service().quote_for_prompt(item.benchmark_prompt)
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
        quoted_amount_cents=quote.official_quote_cents,
        quoted_pwr_amount=quote.pwr_quote,
        quoted_pwr_anchor_price_cents=quote.pwr_anchor_price_cents,
        outputs=list(item.outputs),
        skills=list(item.skills),
        input_files=list(item.input_files),
        featured=item.featured,
    )
