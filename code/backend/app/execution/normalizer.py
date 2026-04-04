"""Intent -> recipe normalization.

Adapted from AgentSkillOS style workflow contracts:
- stable request/response dataclasses
- deterministic transformation with small policy tables
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    ExecutionRecipe,
    ExecutionStep,
    IntentRequest,
    MediaType,
    ResourceEstimate,
)


@dataclass(frozen=True)
class _StepBlueprint:
    provider: str
    model: str
    action: str
    resources: ResourceEstimate


_BLUEPRINTS: dict[MediaType, _StepBlueprint] = {
    MediaType.TEXT: _StepBlueprint(
        provider="builtin",
        model="builtin/text-fast",
        action="generation",
        resources=ResourceEstimate(capacity_units=1, memory_mb=256, expected_duration_ticks=1),
    ),
    MediaType.IMAGE: _StepBlueprint(
        provider="alibaba-mulerouter",
        model="alibaba/wan2.6-t2i",
        action="generation",
        resources=ResourceEstimate(capacity_units=3, memory_mb=2_048, expected_duration_ticks=2),
    ),
    MediaType.VIDEO: _StepBlueprint(
        provider="alibaba-mulerouter",
        model="alibaba/wan2.6-t2v",
        action="generation",
        resources=ResourceEstimate(capacity_units=6, memory_mb=6_144, expected_duration_ticks=4),
    ),
}


def normalize_intent_to_recipe(intent: IntentRequest) -> ExecutionRecipe:
    """Build a deterministic single-step execution recipe for MVP dispatch."""
    outputs = intent.desired_outputs or (MediaType.TEXT,)
    selected_output = outputs[0]
    requested_outputs = ",".join(output.value for output in outputs)
    blueprint = _BLUEPRINTS[selected_output]
    step = ExecutionStep(
        step_id=f"{intent.intent_id}-step-1",
        provider=blueprint.provider,
        model=blueprint.model,
        action=blueprint.action,
        output_type=selected_output,
        resources=blueprint.resources,
        parameters={"prompt": intent.prompt},
    )

    metadata = {
        "normalizer": "execution.normalizer.v1",
        "outputs": requested_outputs,
        "requested_outputs": requested_outputs,
        "primary_output": selected_output.value,
    }
    return ExecutionRecipe(
        recipe_id=f"recipe-{intent.intent_id}",
        source_intent_id=intent.intent_id,
        prompt=intent.prompt,
        steps=(step,),
        metadata=metadata,
    )
