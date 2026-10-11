"""Disposable held-stream ASGI app; never opens a customer database."""
from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from server import _hub_uvicorn_options


@contextlib.asynccontextmanager
async def lifespan(_app):
    yield
    Path(sys.argv[2]).write_text('lifespan-closed\n')


app = FastAPI(lifespan=lifespan)


@app.get('/hold')
async def hold():
    async def body():
        yield b'LIVE\n'
        await asyncio.sleep(3600)
    return StreamingResponse(body(), media_type='text/plain')


if __name__ == '__main__':
    options = _hub_uvicorn_options(
        SimpleNamespace(tls_certificate='', tls_private_key=''),
        '127.0.0.1', int(sys.argv[1]))
    # Test only: 1s bound verifies production's same configuration route.
    options['timeout_graceful_shutdown'] = 1.0
    uvicorn.run(app, **options)
