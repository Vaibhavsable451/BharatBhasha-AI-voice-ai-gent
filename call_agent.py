import argparse
import asyncio
from typing import Any, Optional

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import (
    RealtimeConversationItemMessageUser,
    RealtimeConversationItemMessageUserContent,
    RealtimeConversationItemType,
    RealtimeServerEventError,
    RealtimeServerEventResponseAudioTranscriptDone,
    RealtimeServerEventResponseDone,
    RealtimeServerEventResponseTextDone,
    RealtimeServerEventSessionCreated,
)
from azure.identity.aio import DefaultAzureCredential

PROJECT_ENDPOINT = (
    "https://vhsabl50222e3e2-4943-resource.services.ai.azure.com/"
    "api/projects/vhsabl50222e3e2-4943"
)
AGENT_NAME = "cyan-agent-9j5pxwdwxs"
RESPONSE_TIMEOUT_SECONDS = 45


def event_value(event: Any, name: str) -> Any:
    value = getattr(event, name, None)
    if value is None and hasattr(event, "get"):
        value = event.get(name)
    return value


async def send_message(message: str) -> Optional[str]:
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
            await connection.conversation.item.create(
                item=RealtimeConversationItemMessageUser(
                    type=RealtimeConversationItemType.MESSAGE,
                    content=[
                        RealtimeConversationItemMessageUserContent(
                            type="input_text",
                            text=message,
                        )
                    ],
                )
            )
            await connection.response.create()

            conversation_id: Optional[str] = None

            async def read_response() -> None:
                nonlocal conversation_id
                async for event in connection:
                    if isinstance(event, RealtimeServerEventSessionCreated):
                        conversation_id = event.conversation_id or conversation_id
                    elif isinstance(event, RealtimeServerEventResponseAudioTranscriptDone):
                        print(f"Agent: {event.transcript}")
                    elif isinstance(event, RealtimeServerEventResponseTextDone):
                        print(f"Agent: {event.text}")
                    elif isinstance(event, RealtimeServerEventError):
                        error = event_value(event, "error")
                        detail = event_value(error, "message") or "unknown service error"
                        raise RuntimeError(f"Voice-agent session error: {detail}")
                    elif isinstance(event, RealtimeServerEventResponseDone):
                        return

            await asyncio.wait_for(
                read_response(),
                timeout=RESPONSE_TIMEOUT_SECONDS,
            )
            return conversation_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a text message to the configured Azure voice agent."
    )
    parser.add_argument(
        "-m",
        "--message",
        help="Text to send. If omitted, you will be prompted.",
    )
    args = parser.parse_args()
    message = (args.message or input("You: ")).strip()
    if not message:
        parser.error("message must not be empty")

    conversation_id = asyncio.run(send_message(message))
    if conversation_id:
        print(f"Conversation id: {conversation_id}")


if __name__ == "__main__":
    main()
