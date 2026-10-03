"""Thin client for any OpenAI-compatible chat endpoint.

Stardeck never asks the model for free-form code. Every call that feeds the app goes
through `structured()`, which constrains output to a JSON schema (grammar-constrained
decoding on Ollama / llama.cpp / vLLM) and validates it with Pydantic.
"""

import json
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from stardeck.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)

# Qwen-family reasoning models may emit <think>...</think> before the answer.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(RuntimeError):
    pass


def clean_output(text: str) -> str:
    text = _THINK_RE.sub("", text).strip()
    return _FENCE_RE.sub("", text).strip()


class LLMClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self._http = httpx.Client(
            base_url=self.settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            timeout=self.settings.llm_timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def chat(self, messages: list[dict], response_format: dict | None = None) -> str:
        payload: dict = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
        }
        if self.settings.llm_thinking == "off":
            payload |= {
                "reasoning_effort": "none",  # LM Studio, OpenAI-style servers
                "chat_template_kwargs": {"enable_thinking": False},  # vLLM, llama.cpp, SGLang
                "think": False,  # Ollama
            }
        if response_format:
            payload["response_format"] = response_format
        try:
            resp = self._http.post("/chat/completions", json=payload)
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            content = clean_output(choice["message"].get("content") or "")
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        if not content and choice.get("finish_reason") == "length":
            raise LLMError(
                f"The model used all {self.settings.llm_max_tokens} tokens without answering "
                "(usually long 'thinking'). Set STARDECK_LLM_THINKING=off or raise STARDECK_LLM_MAX_TOKENS."
            )
        return content

    def structured(self, messages: list[dict], model: type[T], max_attempts: int = 2) -> T:
        """Return a validated `model` instance, feeding validation errors back on retry."""
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": model.__name__, "schema": model.model_json_schema(), "strict": True},
        }
        convo = list(messages)
        last_error = ""
        for _ in range(max_attempts):
            raw = self.chat(convo, response_format=response_format)
            try:
                return model.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)
                convo += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Invalid JSON for the schema: {last_error}\nReturn only corrected JSON."},
                ]
        raise LLMError(f"Model did not return valid {model.__name__}: {last_error}")

    def list_models(self) -> list[str]:
        try:
            resp = self._http.get("/models")
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
        except (httpx.HTTPError, KeyError) as exc:
            raise LLMError(f"Could not reach LLM endpoint: {exc}") from exc
