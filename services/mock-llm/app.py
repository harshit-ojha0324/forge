"""Mock OpenAI-compatible model server.

Stands in for vLLM (and for Gemini's OpenAI-compatible endpoint) in local
development so the entire platform — failover included — runs on a laptop
with no GPU. Behaviour is tuned via env:

    MOCK_NAME               backend name echoed in responses (vllm | gemini)
    MOCK_MODEL              model id reported in responses
    MOCK_TTFT_MS            simulated time before the first token
    MOCK_TOKENS_PER_SECOND  streaming pace
    MOCK_COMPLETION_TOKENS  length of generated answer

Runtime failure injection (for demos that don't want to kill the pod):
    POST /control {"fail": true}   -> every request 500s until reset
"""
import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

NAME = os.environ.get("MOCK_NAME", "vllm")
MODEL = os.environ.get("MOCK_MODEL", "qwen2.5-3b-instruct")
TTFT_MS = int(os.environ.get("MOCK_TTFT_MS", "80"))
TOKENS_PER_SECOND = int(os.environ.get("MOCK_TOKENS_PER_SECOND", "60"))
COMPLETION_TOKENS = int(os.environ.get("MOCK_COMPLETION_TOKENS", "40"))

app = FastAPI(title=f"mock-{NAME}")
state = {"fail": False}

WORDS = (
    "the quick brown fox jumps over the lazy dog and keeps running through "
    "the city streets past sensors cameras and traffic lights collecting data"
).split()


def _completion_text(prompt: str) -> str:
    words = [WORDS[i % len(WORDS)] for i in range(COMPLETION_TOKENS)]
    return f"[{NAME}] re: '{prompt[:40]}' -> " + " ".join(words)


def _estimate_prompt_tokens(messages: list) -> int:
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return max(chars // 4, 1)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "backend": NAME, "failing": state["fail"]}


@app.post("/control")
async def control(request: Request):
    body = await request.json()
    state["fail"] = bool(body.get("fail", False))
    return {"backend": NAME, "failing": state["fail"]}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    if state["fail"]:
        return JSONResponse(
            {"error": {"message": f"{NAME} injected failure", "type": "server_error"}},
            status_code=500,
        )
    payload = await request.json()
    messages = payload.get("messages", [])
    prompt = str(messages[-1].get("content", "")) if messages else ""
    text = _completion_text(prompt)
    prompt_tokens = _estimate_prompt_tokens(messages)
    completion_tokens = COMPLETION_TOKENS
    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if payload.get("stream"):
        return StreamingResponse(
            _stream(request_id, created, text, prompt_tokens, completion_tokens),
            media_type="text/event-stream",
        )

    await asyncio.sleep(TTFT_MS / 1000 + completion_tokens / TOKENS_PER_SECOND)
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


async def _stream(request_id, created, text, prompt_tokens, completion_tokens):
    def chunk(delta: dict, finish=None, usage=None) -> str:
        body = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            body["usage"] = usage
        return f"data: {json.dumps(body)}\n\n"

    await asyncio.sleep(TTFT_MS / 1000)
    yield chunk({"role": "assistant", "content": ""})
    for word in text.split(" "):
        await asyncio.sleep(1 / TOKENS_PER_SECOND)
        yield chunk({"content": word + " "})
    yield chunk(
        {},
        finish="stop",
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    )
    yield "data: [DONE]\n\n"
