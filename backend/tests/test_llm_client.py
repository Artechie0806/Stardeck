import json

import httpx
import pytest
from pydantic import BaseModel

from stardeck.config import Settings
from stardeck.llm import LLMClient, LLMError


class SQLAnswer(BaseModel):
    reasoning: str
    sql: str


def make_client(replies: list[str]) -> tuple[LLMClient, list[dict]]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        content = replies[len(sent) - 1]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return LLMClient(Settings(), transport=httpx.MockTransport(handler)), sent


def test_structured_strips_think_and_fences():
    client, sent = make_client(['<think>hmm</think>```json\n{"reasoning": "r", "sql": "SELECT 1"}\n```'])
    answer = client.structured([{"role": "user", "content": "q"}], SQLAnswer)
    assert answer.sql == "SELECT 1"
    assert sent[0]["response_format"]["type"] == "json_schema"


def test_structured_retries_with_validation_error():
    client, sent = make_client(['{"sql": 5}', '{"reasoning": "r", "sql": "SELECT 1"}'])
    answer = client.structured([{"role": "user", "content": "q"}], SQLAnswer)
    assert answer.sql == "SELECT 1"
    assert len(sent) == 2
    assert "Invalid JSON" in sent[1]["messages"][-1]["content"]


def test_structured_gives_up():
    client, _ = make_client(["nope", "still nope"])
    with pytest.raises(LLMError):
        client.structured([{"role": "user", "content": "q"}], SQLAnswer)


def test_thinking_off_sends_switches_and_cap():
    client, sent = make_client(['{"reasoning": "r", "sql": "SELECT 1"}'])
    client.structured([{"role": "user", "content": "q"}], SQLAnswer)
    assert sent[0]["reasoning_effort"] == "none" and sent[0]["think"] is False
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False} and sent[0]["max_tokens"] == 4096


def test_runaway_thinking_is_a_clear_error():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})

    client = LLMClient(Settings(llm_thinking="server"), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="without answering"):
        client.chat([{"role": "user", "content": "q"}])
