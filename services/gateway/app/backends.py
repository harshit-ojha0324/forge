"""Upstream model backends.

Both the primary (self-hosted vLLM) and the fallback (Gemini via Google's
OpenAI-compatible endpoint) speak the OpenAI chat-completions protocol,
so one client class covers both. The gateway owns model naming: clients
send the public alias, and each backend rewrites it to the model it
actually serves.
"""
import httpx


class BackendError(Exception):
    """Any upstream failure that should count against the circuit breaker."""

    def __init__(self, backend: str, detail: str):
        super().__init__(f"[{backend}] {detail}")


def _is_backend_fault(status: int) -> bool:
    # 5xx, plus 401/403: upstream rejected the *gateway's* credentials,
    # which the caller can't fix — fail over and count it, don't echo it
    # back as if the caller's own key were bad.
    return status >= 500 or status in (401, 403)


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
        if _is_backend_fault(response.status_code):
            raise BackendError(self.name, f"upstream {response.status_code}")
        if response.status_code >= 400:
            # Other 4xx is the caller's fault (bad request, context too long):
            # surface it, don't trip the breaker or retry elsewhere.
            raise UpstreamClientError(response.status_code, response.text)
        # A 2xx that isn't a JSON object (proxy error page, truncated body)
        # is an upstream failure: it must reach the breaker and fail over,
        # or a half-open probe never resolves and the primary stays off.
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise BackendError(self.name, "upstream 2xx with a non-JSON-object body")
        return body

    async def start_stream(self, payload: dict) -> httpx.Response:
        """Open a streaming response, returned only once upstream accepted
        it (2xx): failover is still cheap until a byte reaches the client."""
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
        if _is_backend_fault(response.status_code):
            await response.aread()
            await response.aclose()
            raise BackendError(self.name, f"upstream {response.status_code}")
        if response.status_code >= 400:
            body = await response.aread()
            await response.aclose()
            raise UpstreamClientError(response.status_code, body.decode(errors="replace"))
        return response


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
