from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

ROOT = Path(__file__).resolve().parent
STATIC_FILES = {"app.js", "mic-worklet.js", "style.css"}
PROJECT_ENDPOINT = os.getenv(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://vhsabl50222e3e2-4943-resource.services.ai.azure.com/"
    "api/projects/vhsabl50222e3e2-4943",
)
AGENT_NAME = os.getenv("AZURE_VOICE_AGENT_NAME", "cyan-agent-9j5pxwdwxs")
SAMPLE_RATE = 24000
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGES = 3
MAX_SESSION_IMAGES = 5
MAX_BROWSER_MESSAGE = (MAX_IMAGE_BYTES * 4 // 3 + 1024) * MAX_IMAGES + 32 * 1024
MAX_PCM_CHUNK_BYTES = 9600
IMAGE_DATA_URL = re.compile(
    r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/]*={0,2})\Z"
)
logger = logging.getLogger(__name__)


def _image_mime_from_bytes(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_image_data_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > (MAX_IMAGE_BYTES * 4 // 3) + 64:
        raise ValueError("Each image must be no larger than 4 MiB.")
    match = IMAGE_DATA_URL.fullmatch(value)
    if not match:
        raise ValueError("Use a JPEG, PNG, or WebP image.")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("The uploaded image data is invalid.") from error
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Each image must be no larger than 4 MiB.")
    if _image_mime_from_bytes(data) != match.group(1):
        raise ValueError("The image bytes do not match the declared file type.")
    return value


def _event_value(event: Any, name: str) -> Any:
    value = getattr(event, name, None)
    if value is None and hasattr(event, "get"):
        value = event.get(name)
    return value


def _event_type(event: Any) -> str:
    value = _event_value(event, "type")
    return str(getattr(value, "value", value))


def _audio_bytes(delta: Any) -> bytes:
    if isinstance(delta, bytes):
        return delta
    if isinstance(delta, str):
        return base64.b64decode(delta, validate=True)
    raise RuntimeError("The voice service returned an unsupported audio chunk.")


def _image_context_content(packet: dict[str, Any]) -> list[dict[str, str]]:
    images = packet.get("images", [])
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError(f"Attach no more than {MAX_IMAGES} images.")
    for image in images:
        _validate_image_data_url(image)
    if not images:
        raise ValueError("Add at least one image to share.")
    content = [
        {
            "type": "input_text",
            "text": (
                "The user shared this image as visual context for a spoken question. "
                "Wait for the user to ask about it before answering."
            ),
        }
    ]
    content.extend(
        {"type": "input_image", "image_url": image, "detail": "low"}
        for image in images
    )
    return content


@web.middleware
async def local_only(request: web.Request, handler):
    expected_hosts = {
        f"127.0.0.1:{request.app['port']}",
        f"localhost:{request.app['port']}",
    }
    if request.host not in expected_hosts:
        raise web.HTTPForbidden(text="Use the localhost address printed by the server.")
    origin = request.headers.get("Origin")
    if origin and origin != f"{request.scheme}://{request.host}":
        raise web.HTTPForbidden(text="Cross-origin requests are not allowed.")
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(ROOT / "static" / "index.html")


async def asset(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if name not in STATIC_FILES:
        raise web.HTTPNotFound()
    return web.FileResponse(ROOT / "static" / name)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def relay_session(socket: web.WebSocketResponse) -> None:
    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=PROJECT_ENDPOINT,
            credential=credential,
            allow_preview=True,
        ) as client,
    ):
        async with client.voice_agents.realtime.connect(
            agent_name=AGENT_NAME
        ) as connection:
            await socket.send_json({"type": "ready", "agent": AGENT_NAME})
            response_active = False
            shared_image_count = 0

            async def from_agent() -> None:
                nonlocal response_active
                async for event in connection:
                    kind = _event_type(event)
                    if kind == "session.created":
                        await socket.send_json(
                            {
                                "type": "conversation",
                                "id": _event_value(event, "conversation_id"),
                            }
                        )
                    elif kind == "response.created":
                        response_active = True
                        await socket.send_json({"type": "response_start"})
                    elif kind == "input_audio_buffer.speech_started":
                        await socket.send_json({"type": "interrupt"})
                    elif kind == "conversation.item.input_audio_transcription.completed":
                        await socket.send_json(
                            {
                                "type": "transcript",
                                "role": "user",
                                "text": _event_value(event, "transcript") or "",
                            }
                        )
                    elif kind in {
                        "response.output_audio_transcript.delta",
                        "response.output_text.delta",
                    }:
                        await socket.send_json(
                            {
                                "type": "assistant_delta",
                                "text": _event_value(event, "delta") or "",
                            }
                        )
                    elif kind == "response.output_audio_transcript.done":
                        text = _event_value(event, "transcript") or ""
                        if text:
                            await socket.send_json(
                                {"type": "assistant_final", "text": text}
                            )
                    elif kind == "response.output_text.done":
                        text = _event_value(event, "text") or ""
                        if text:
                            await socket.send_json(
                                {"type": "assistant_final", "text": text}
                            )
                    elif kind == "response.output_audio.delta":
                        await socket.send_bytes(
                            _audio_bytes(_event_value(event, "delta"))
                        )
                    elif kind == "response.done":
                        response_active = False
                        response = _event_value(event, "response")
                        await socket.send_json(
                            {
                                "type": "done",
                                "status": _event_value(response, "status") or "completed",
                            }
                        )
                    elif kind == "error":
                        error = _event_value(event, "error")
                        message = _event_value(error, "message") or "Voice service error."
                        code = _event_value(error, "code")
                        if code == "conversation_already_has_active_response":
                            response_active = True
                            await socket.send_json(
                                {
                                    "type": "notice",
                                    "message": "The agent is finishing its current reply. Please wait.",
                                }
                            )
                        else:
                            response_active = False
                            await socket.send_json(
                                {"type": "error", "message": str(message)[:300]}
                            )

            async def from_browser() -> None:
                nonlocal response_active, shared_image_count
                async for message in socket:
                    if message.type == WSMsgType.BINARY:
                        data = message.data
                        if (
                            not data
                            or len(data) > MAX_PCM_CHUNK_BYTES
                            or len(data) % 2
                        ):
                            await socket.send_json(
                                {"type": "error", "message": "Invalid PCM16 microphone chunk."}
                            )
                            continue
                        await connection.input_audio_buffer.append(audio=data)
                    elif message.type == WSMsgType.TEXT:
                        try:
                            packet = json.loads(message.data)
                        except json.JSONDecodeError:
                            await socket.send_json(
                                {"type": "error", "message": "Invalid message format."}
                            )
                            continue
                        if not isinstance(packet, dict) or packet.get("type") != "images":
                            await socket.send_json(
                                {"type": "error", "message": "Unsupported message type."}
                            )
                            continue
                        try:
                            content = _image_context_content(packet)
                        except ValueError as error:
                            await socket.send_json(
                                {"type": "error", "message": str(error)}
                            )
                            continue
                        image_count = len(packet["images"])
                        if shared_image_count + image_count > MAX_SESSION_IMAGES:
                            await socket.send_json(
                                {
                                    "type": "error",
                                    "message": f"Share no more than {MAX_SESSION_IMAGES} images per session.",
                                }
                            )
                            continue
                        if response_active:
                            await socket.send_json(
                                {"type": "notice", "message": "Wait for the current reply to finish."}
                            )
                            continue
                        await connection.send(
                            {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": content,
                                },
                            }
                        )
                        shared_image_count += image_count
                        await socket.send_json(
                            {
                                "type": "image_context_ready",
                                "count": image_count,
                                "total": shared_image_count,
                            }
                        )

            tasks = [
                asyncio.create_task(from_agent()),
                asyncio.create_task(from_browser()),
            ]
            try:
                done, _ = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


async def voice(request: web.Request) -> web.WebSocketResponse:
    socket = web.WebSocketResponse(
        max_msg_size=MAX_BROWSER_MESSAGE,
        heartbeat=20,
    )
    await socket.prepare(request)
    try:
        await relay_session(socket)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Realtime voice session failed")
        if not socket.closed:
            await socket.send_json(
                {
                    "type": "error",
                    "message": "Could not connect to the voice agent. Check the server terminal and Azure sign-in.",
                }
            )
    finally:
        await socket.close()
    return socket


def create_app(port: int = 8780) -> web.Application:
    app = web.Application(
        middlewares=[local_only],
        client_max_size=MAX_BROWSER_MESSAGE,
    )
    app["port"] = port
    app.add_routes(
        [
            web.get("/", index),
            web.get("/static/{name}", asset),
            web.get("/healthz", health),
            web.get("/ws", voice),
        ]
    )
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("azure").setLevel(logging.WARNING)
    port = int(os.getenv("PORT", "8780"))
    if not 1024 <= port <= 65535:
        raise ValueError("PORT must be between 1024 and 65535.")
    print(f"Open http://127.0.0.1:{port}")
    web.run_app(create_app(port), host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
