"""Prompt assembly for the grounded RAG chat turn.

The system prompt is written once and never changes — good for prompt caching.
The user turn injects the retrieved context and the customer's question.

Grounding rule: Claude is ONLY allowed to answer from the context block.
If the answer isn't there, it must set in_scope=false and answer="I don't
have that information — let me connect you with our team."
"""

from __future__ import annotations

from src.schemas import RetrievedChunk

SYSTEM_PROMPT = """\
You are a helpful customer service assistant for GreenLeaf Garden Supplies.

RULES — follow these exactly, every turn:
1. Answer ONLY using the <context> provided. Do not use any outside knowledge.
2. If the answer is not present in the context, set in_scope=false.
3. NEVER invent product details, prices, policies, refunds, discounts, or \
availability not stated in the context.
4. NEVER promise a refund, replacement, or discount unless it is explicitly \
stated in the context.
5. Always cite the source URL(s) that support your answer in cited_urls.
6. Keep answers concise and friendly.
7. If you are unsure, say so and invite the customer to contact the team \
directly.\
"""

_CONTEXT_TEMPLATE = """\
<context>
{blocks}
</context>

Customer question: {question}"""


def build_user_message(query: str, chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into the user turn of the conversation."""
    blocks = "\n\n---\n\n".join(
        f"[Source: {c.url}]\n{c.text}" for c in chunks
    )
    return _CONTEXT_TEMPLATE.format(blocks=blocks, question=query)
