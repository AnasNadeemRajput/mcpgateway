"""
task3_streaming_guardrail/streamhandler.py

The actual streaming guardrail endpoint.
"""

import asyncio
import logging
import time
from typing import AsyncIterator, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from task3_streaming_guardrail.redaction.chunk_buffer import ChunkBuffer

logger = logging.getLogger("task3_streaming_guardrail")

router = APIRouter(prefix="/stream", tags=["streaming-guardrail"])


# ---------------------------------------------------------------------------
# Upstream LLM interface + mock implementation
# ---------------------------------------------------------------------------

class UpstreamLLMClient:
    """Interface any real provider adapter should implement."""
    async def stream_completion(self, prompt: str) -> AsyncIterator[str]:
        raise NotImplementedError
        yield


class MockUpstreamLLMClient(UpstreamLLMClient):
    """Streams a canned response word-by-word with realistic delays."""
    
    _RESPONSE_PIECES = [
        "Sure, ", "I can ", "help with ", "that. ",
        "Please ", "reach out ", "to our ", "support team ",
        "at john.doe@ex", "ample.com ", "or call ", "them ",
        "regarding invoice ", "ending in ", "4242 4242 4242 4242 ",
        "and reference ", "SSN 123-45-6789 ", "on file ",
        "if needed. ", "We're ", "happy ", "to help ", "further.",
    ]

    async def stream_completion(self, prompt: str) -> AsyncIterator[str]:
        for piece in self._RESPONSE_PIECES:
            await asyncio.sleep(0.03)
            yield piece


_upstream = MockUpstreamLLMClient()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class StreamCompletionRequest(BaseModel):
    prompt: str


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

async def _redacted_stream(prompt: str) -> AsyncIterator[str]:
    buffer = ChunkBuffer()
    request_start = time.monotonic()
    first_byte_at: Optional[float] = None
    chunk_count = 0

    async for raw_chunk in _upstream.stream_completion(prompt):
        chunk_count += 1
        for safe_piece in buffer.feed(raw_chunk):
            if first_byte_at is None:
                first_byte_at = time.monotonic()
                logger.info("TTFT: %.1fms", (first_byte_at - request_start) * 1000)
            yield safe_piece

    for safe_piece in buffer.finalize():
        if first_byte_at is None:
            first_byte_at = time.monotonic()
        yield safe_piece

    total_duration_ms = (time.monotonic() - request_start) * 1000
    logger.info(
        "Stream complete: %d upstream chunks, %.1fms total",
        chunk_count, total_duration_ms,
    )


@router.post("/completion")
async def stream_completion(request: StreamCompletionRequest):
    """Streams a redacted completion back to the client."""
    return StreamingResponse(
        _redacted_stream(request.prompt),
        media_type="text/plain; charset=utf-8",
    )