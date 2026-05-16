"""Claude API client — structured output via tool use with prompt caching.

Claude is forced to call the `respond` tool on every turn, which gives us a
guaranteed JSON payload that maps directly to `ChatResponse`.  The system
prompt is sent with `cache_control: ephemeral` so repeated calls within the
5-minute TTL window hit the cache rather than re-processing the instructions.
"""

from __future__ import annotations

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.chat.prompt import SYSTEM_PROMPT, build_user_message
from src.config import get_settings
from src.logging_setup import get_logger
from src.schemas import ChatResponse, RetrievedChunk

log = get_logger(__name__)

# Tool definition that forces Claude to return a structured ChatResponse.
_RESPOND_TOOL: dict = {
    "name": "respond",
    "description": (
        "Return your answer to the customer. Always call this tool — never reply "
        "with plain text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Your answer to the customer's question.",
            },
            "cited_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "URLs from the context that directly support your answer. "
                    "Only include URLs that appear verbatim in the [Source: ...] tags."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "Your confidence in the answer, between 0.0 and 1.0.",
            },
            "in_scope": {
                "type": "boolean",
                "description": (
                    "True if the question could be answered from the provided context. "
                    "False if the context does not contain the relevant information."
                ),
            },
        },
        "required": ["answer", "cited_urls", "confidence", "in_scope"],
    },
}


class ClaudeClient:
    """Wraps the Anthropic SDK. Satisfies ClaudeClientProtocol."""

    def __init__(self) -> None:
        s = get_settings()
        self._client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        self._model = s.claude_model

    @retry(
        retry=retry_if_exception_type(anthropic.APIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def complete(self, query: str, chunks: list[RetrievedChunk]) -> ChatResponse:
        """Call Claude with a grounded prompt and return a validated ChatResponse."""
        user_message = build_user_message(query, chunks)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        # Cache the system prompt — it never changes between turns.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[_RESPOND_TOOL],
                tool_choice={"type": "tool", "name": "respond"},
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIError as e:
            log.error("claude.api_error", error=str(e))
            raise

        # Extract the tool_use block — guaranteed present because tool_choice forces it.
        tool_block = next(
            (b for b in response.content if b.type == "tool_use"),
            None,
        )
        if tool_block is None:
            raise ValueError("Claude response contained no tool_use block")

        raw: dict = tool_block.input  # type: ignore[attr-defined]

        # Clamp confidence to [0, 1] in case Claude returns e.g. 1.2.
        raw["confidence"] = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))

        result = ChatResponse(**raw)
        log.info(
            "claude.complete",
            in_scope=result.in_scope,
            confidence=result.confidence,
            cited_count=len(result.cited_urls),
        )
        return result
