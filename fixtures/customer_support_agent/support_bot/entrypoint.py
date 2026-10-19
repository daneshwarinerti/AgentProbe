"""Main entrypoint for customer support multi-agent system."""

from typing import Any
from support_bot.agents import handle_triage


def run_agent(prompt: str = "", **kwargs: Any) -> dict[str, Any]:
    """Execute the multi-agent customer support workflow.
    
    Compatible with AgentProbe text input mapping and kwargs mapping.
    """
    user_input = prompt or kwargs.get("input") or kwargs.get("query") or "Hello"
    if isinstance(user_input, dict):
        user_input = user_input.get("prompt", str(user_input))

    final_answer = handle_triage(str(user_input))

    return {
        "final_answer": final_answer,
        "output": final_answer,
        "status": "success",
    }

