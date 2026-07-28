"""Versioned, bounded prompts for the dataset agent control loop."""

from .next_action_v1 import (
    AGENT_NEXT_ACTION_PROMPT_VERSION,
    AGENT_NEXT_ACTION_SYSTEM_PROMPT,
    build_next_action_user_prompt,
)

__all__ = (
    "AGENT_NEXT_ACTION_PROMPT_VERSION",
    "AGENT_NEXT_ACTION_SYSTEM_PROMPT",
    "build_next_action_user_prompt",
)
