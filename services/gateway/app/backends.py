"""Upstream model backends.

Both the primary (self-hosted vLLM) and the fallback (Gemini via Google's
OpenAI-compatible endpoint) speak the OpenAI chat-completions protocol,
so one client class covers both. The gateway owns model naming: clients
send the public alias, and each backend rewrites it to the model it
actually serves.
"""
from typing import AsyncIterator

import httpx


class BackendError(Exception):
    """Any upstream failure that should count against the circuit breaker."""

    def __init__(self, backend: str, detail: str):
        super().__init__(f"[{backend}] {detail}")
        self.backend = backend
        self.detail = detail


class StreamHandle:
    """An open, status-validated streaming response.

    Created only after the upstream accepted the request (2xx), so a
    failover decision can still be made cheaply before any byte reaches
    the client. Iterate `lines()` to forward SSE data.
    """

    def __init__(self, backend: str, response: httpx.Response):
        self.backend = backend
        self._response = response

    async def lines(self) -> AsyncIterator[str]:
        async for line in self._response.aiter_lines():
            yield line

    async def close(self) -> None:
        await self._response.aclose()


class Backend:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def _rewrite(self, payload: dict) -> dict:
        rewritten = dict(payload)
        rewritten["model"] = self.model
        if payload.get("stream"):
            # Ask vLLM/OpenAI-compatible servers to append a usage chunk
            # so streamed requests are metered exactly, not estimated.
            rewritten.setdefault("stream_options", {"include_usage": True})
        return rewritten

    async def chat(self, payload: dict) -> dict:
        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                json=self._rewrite(payload),
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise BackendError(self.name, f"transport error: {exc!r}") from exc
        if response.status_code >= 500:
            raise BackendError(self.name, f"upstream {response.status_code}")
        if response.status_code >= 400:
            # 4xx is the caller's fault (bad request, context too long):
            # surface it, don't trip the breaker or retry elsewhere.
            raise UpstreamClientError(response.status_code, response.text)
        return response.json()

    async def start_stream(self, payload: dict) -> StreamHandle:
        request = self._client.build_request(
            "POST",
            f"{self.base_url}/chat/completions",
            json=self._rewrite(payload),
            headers=self._headers,
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise BackendError(self.name, f"transport error: {exc!r}") from exc
        if response.status_code >= 500:
            await response.aread()
            await response.aclose()
            raise BackendError(self.name, f"upstream {response.status_code}")
        if response.status_code >= 400:
            body = await response.aread()
            await response.aclose()
            raise UpstreamClientError(response.status_code, body.decode(errors="replace"))
        return StreamHandle(self.name, response)


class UpstreamClientError(Exception):
    """4xx from upstream — passed through to the caller as-is."""

    def __init__(self, status_code: int, body: str):
        super().__init__(f"upstream client error {status_code}")
        self.status_code = status_code
        self.body = body


def extract_usage(response: dict) -> tuple[int, int]:
    usage = response.get("usage") or {}
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def estimate_tokens(text: str) -> int:
    """Rough fallback when an upstream omits usage: ~4 chars per token."""
    return max(len(text) // 4, 1)
