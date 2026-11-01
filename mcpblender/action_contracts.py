"""Public action invocation; compiled steps are never accepted from MCP callers."""
from pydantic import Field

from .config import Model


class RunAction(Model):
    action: str = Field(min_length=1, max_length=64)
    inputs: dict = Field(default_factory=dict)


COMMANDS = {"run_action": (RunAction, (), "Run a discovered, validated named action as one tracked operation. Discover typed inputs with describe_action; inspect partial results on failure/cancellation.")}
