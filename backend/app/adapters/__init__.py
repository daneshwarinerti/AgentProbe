"""Pluggable interfaces for executing agents from local, HTTP, or Docker targets."""

from backend.app.adapters.base import AgentAdapter
from backend.app.adapters.config import load_agent_config, make_adapter

__all__ = ["AgentAdapter", "load_agent_config", "make_adapter"]
