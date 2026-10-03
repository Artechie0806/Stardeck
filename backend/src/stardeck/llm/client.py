"""Thin client for any OpenAI-compatible chat endpoint.

Stardeck never asks the model for free-form code. Every call that feeds the app goes
through `structured()`, which constrains output to a JSON schema (grammar-constrained
decoding on Ollama / llama.cpp / vLLM) and validates it with Pydantic.
"""

import json
import logging
import re
import threading
from contextlib import nullcontext
from typing import TYPE_CHECKING, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from stardeck.config import Settings, get_settings

if TYPE_CHECKING:
    from stardeck.llm.gate import Gate
    from stardeck.llm.status import LLMStatus

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger("stardeck.llm")

# Qwen-family reasoning models may emit <think>...</think> before the answer.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(RuntimeError):
    pass


class LLMBusy(LLMError):
    """Too many AI requests right now (see llm/gate.py)."""

    def __init__(self) -> None:
        super().__init__("the AI is busy with other requests; try again in a minute")


class LLMCancelled(LLMError):
    """The person went away (closed the page) before the AI got to this."""

    def __init__(self) -> None:
        super().__init__("cancelled")


class LLMUnavailable(LLMError):
    """The AI server was just found offline (it's checked again shortly)."""

    def __init__(self) -> None:
        super().__init__("the AI server is offline right now")


def ai_message(exc: LLMError) -> str:
    """What to tell people when the AI couldn't help."""
    if isinstance(exc, LLMBusy):
        return "The AI is busy with other requests. Try again in a minute."
    if isinstance(exc, LLMUnavailable):
        return "The AI is offline right now. Dashboards, filters and sharing keep working."
    if isinstance(exc, LLMCancelled):
        return "Stopped."
    return f"The AI model is not available: {exc}"


def describe_failure(exc: Exception) -> str:
    """What went wrong, for people. Never the server's address (that's logged instead)."""
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return "the AI server can't be reached right now"
    if isinstance(exc, httpx.TimeoutException):
        return "the AI server took too long to answer"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"the AI server returned an error ({exc.response.status_code})"
    return "the AI server sent an unexpected reply"


def clean_output(text: str) -> str:
    text = _THINK_RE.sub("", text).strip()
    return _FENCE_RE.sub("", text).strip()


class LLMClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None,
                 timeout: httpx.Timeout | None = None, *, gate: "Gate | None" = None,
                 status: "LLMStatus | None" = None, background: bool = False,
                 cancel: threading.Event | None = None, wait_s: float | None = None):
        """`gate`: the shared queue in front of the AI server; `status`: fail fast while it's known
        to be offline; `background`: work nobody is waiting on word by word (never takes the last
        slot); `cancel`: set when the person went away, so queued calls don't run."""
        self.settings = settings or get_settings()
        self.gate, self.status, self.background, self.cancel = gate, status, background, cancel
        self.wait_s = wait_s  # longest wait for a free slot (default: the gate's)
        self._http = httpx.Client(
            base_url=self.settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            # Connecting fails fast (an AI server that's off shouldn't hold a request for minutes);
            # answering may take long.
            timeout=timeout or httpx.Timeout(connect=5.0, read=self.settings.llm_timeout_s, write=10.0, pool=5.0),
            transport=transport or httpx.HTTPTransport(retries=1),  # retries connection errors only
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
        if self.status is not None and self.status.offline():
            raise LLMUnavailable
        with self.gate.slot(self.background, self.wait_s, self.cancel) if self.gate else nullcontext():
            if self.cancel is not None and self.cancel.is_set():
                raise LLMCancelled
            try:
                resp = self._http.post("/chat/completions", json=payload)
                resp.raise_for_status()
                choice = resp.json()["choices"][0]
                content = clean_output(choice["message"].get("content") or "")
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                log.warning("LLM request failed: %r", exc)
                if self.status is not None and isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
                    self.status.mark(False)
                raise LLMError(describe_failure(exc)) from exc
        if self.status is not None:
            self.status.mark(True)
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
            log.warning("LLM model list failed: %r", exc)
            raise LLMError(describe_failure(exc)) from exc
