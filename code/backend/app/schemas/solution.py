from pydantic import BaseModel, Field


class SolutionResponse(BaseModel):
    id: str
    title: str
    summary: str
    category: str
    benchmark_task_name: str
    benchmark_description: str
    benchmark_prompt: str
    best_fit: str
    estimated_minutes: int = Field(ge=1)
    price_range: str
    outputs: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    input_files: list[str] = Field(default_factory=list)
    featured: bool = False
