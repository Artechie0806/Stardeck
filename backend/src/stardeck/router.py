"""Pick the assistant agent for a chat message: the dashboard builder or the data analyst.

The model makes the call in one tiny schema-constrained request, so neither agent's prompt has to
carry the other's job. Only conversation state skips it: answering the builder's own question, or a
button that already says which agent it wants.
"""

from typing import Literal

from pydantic import BaseModel, Field

from stardeck.llm import LLMClient

Agent = Literal["builder", "analyst"]


class Route(BaseModel):
    agent: Agent = Field(description="builder = change the dashboard; analyst = answer a question about the data.")


ROUTER_PROMPT = """Two assistants work inside a dashboard tool. Pick the one for the user's latest message.

builder: changes the dashboard or what the app knows.
- wants a chart, number or table added ("add revenue by month", "show top customers", or a bare phrase
  naming data like "orders by status");
- changes, restyles, renames or removes a widget ("make it a donut", "only top 5", "remove refunds");
- tells what a column or code means ("sts 9 means refunded");
- asks what a widget on the dashboard shows or how it is calculated.

analyst: answers a question about the data in words and numbers, without changing the dashboard.
- "what was revenue last month?", "which city grew fastest this year?", "how many customers ordered
  twice?", "why did orders drop in March?", "is the refund rate going up?", "compare Mumbai and Delhi",
  "summarise how we did this quarter".

Widgets on this dashboard: {widgets}"""


def route(llm: LLMClient, message: str, history: list[dict], widget_titles: list[str]) -> Agent:
    messages = [{"role": "system", "content": ROUTER_PROMPT.format(widgets="; ".join(widget_titles) or "none")}]
    for h in history[-2:]:  # a little context for follow-ups like "and for Delhi?"
        if h.get("role") in ("user", "assistant") and h.get("text"):
            messages.append({"role": h["role"], "content": str(h["text"])[:300]})
    messages.append({"role": "user", "content": message})
    return llm.structured(messages, Route).agent
