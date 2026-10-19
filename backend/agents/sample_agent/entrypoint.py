"""Adapter-compatible entry point for the bundled LangGraph sample agent."""

from __future__ import annotations

from backend.agents.sample_agent.agent import SampleAgent


async def run(case_input: str) -> dict[str, object]:
    """Execute one input and return the adapter result wire format."""
    result = await SampleAgent().run(
        case_input,
        temperature=0.0,
        seed=17,
        max_steps=5,
    )
    return {
        "final_answer": result.final_answer,
        "steps": [step.model_dump(exclude_none=True) for step in result.steps],
        "error": result.error,
    }
