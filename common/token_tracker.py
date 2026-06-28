"""Pipeline-wide LLM token usage tracking.

A ContextVar-based tracker that is activated once per pipeline run and
automatically receives usage records from every LangChain LLM call that was
made with a callback-aware client (i.e. any client obtained via
get_llm_client() while the tracker is active).

Usage pattern in the top-level pipeline:
    tracker = TokenUsageTracker.activate()
    # ... run agents ...
    usages = tracker.get_all()

Each agent calls get_llm_client(agent_name="my_agent") and the callback is
attached automatically when a tracker is active.
"""

from __future__ import annotations

import contextvars
import logging
from threading import Lock
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Module-level context variable — set once per pipeline run.
_active_tracker: contextvars.ContextVar[Optional["TokenUsageTracker"]] = contextvars.ContextVar(
    "_active_tracker", default=None
)


class AgentTokenUsage(BaseModel):
    agent_name: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelTokenSummary(BaseModel):
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0


class TokenUsageTracker:
    """Accumulates LLM token usage across all agents in a pipeline run."""

    def __init__(self) -> None:
        self._usages: dict[str, AgentTokenUsage] = {}
        self._lock = Lock()

    @classmethod
    def activate(cls) -> "TokenUsageTracker":
        """Create a new tracker and set it as the active one for this context."""
        tracker = cls()
        _active_tracker.set(tracker)
        return tracker

    @classmethod
    def current(cls) -> Optional["TokenUsageTracker"]:
        """Return the currently-active tracker, or None if none is set."""
        return _active_tracker.get()

    def record(
        self,
        agent_name: str,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        key = f"{agent_name}/{model}"
        with self._lock:
            if key not in self._usages:
                self._usages[key] = AgentTokenUsage(
                    agent_name=agent_name, model=model, provider=provider
                )
            u = self._usages[key]
            u.input_tokens += input_tokens
            u.output_tokens += output_tokens
            u.calls += 1

    def get_all(self) -> list[AgentTokenUsage]:
        """Return per-agent usage sorted by agent name."""
        with self._lock:
            return sorted(self._usages.values(), key=lambda x: x.agent_name)

    def summary_by_model(self) -> list[ModelTokenSummary]:
        """Aggregate token usage by model across all agents."""
        by_model: dict[str, ModelTokenSummary] = {}
        with self._lock:
            for u in self._usages.values():
                if u.model not in by_model:
                    by_model[u.model] = ModelTokenSummary(model=u.model, provider=u.provider)
                s = by_model[u.model]
                s.input_tokens += u.input_tokens
                s.output_tokens += u.output_tokens
                s.total_tokens += u.total_tokens
                s.calls += u.calls
        return sorted(by_model.values(), key=lambda x: x.model)

    def overall_totals(self) -> dict[str, int]:
        all_usages = self.get_all()
        return {
            "input_tokens": sum(u.input_tokens for u in all_usages),
            "output_tokens": sum(u.output_tokens for u in all_usages),
            "total_tokens": sum(u.total_tokens for u in all_usages),
        }


class TokenTrackingCallbackHandler(BaseCallbackHandler):
    """LangChain callback that records token usage into the active TokenUsageTracker.

    Attached to LLM clients when a tracker is active (via get_llm_client()).
    Supports both OpenAI-style llm_output and LangChain's newer usage_metadata.
    """

    def __init__(self, agent_name: str, model: str, provider: str) -> None:
        super().__init__()
        self.agent_name = agent_name
        self.model = model
        self.provider = provider

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tracker = _active_tracker.get()
        if tracker is None:
            return

        input_tokens = 0
        output_tokens = 0

        # OpenAI-style: llm_output carries token_usage
        if response.llm_output and isinstance(response.llm_output, dict):
            usage = response.llm_output.get("token_usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

        # LangChain 0.3+ AIMessage.usage_metadata (provider-agnostic)
        if input_tokens == 0 and output_tokens == 0:
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "message") and hasattr(gen.message, "usage_metadata"):
                        um = gen.message.usage_metadata or {}
                        input_tokens += um.get("input_tokens", 0)
                        output_tokens += um.get("output_tokens", 0)

        if input_tokens > 0 or output_tokens > 0:
            tracker.record(self.agent_name, self.model, self.provider, input_tokens, output_tokens)
            log.debug(
                "Token usage recorded: agent=%s model=%s in=%d out=%d",
                self.agent_name, self.model, input_tokens, output_tokens,
            )
