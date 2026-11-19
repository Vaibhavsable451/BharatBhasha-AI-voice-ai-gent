# Azure voice agent

The browser app connects to the existing `cyan-agent-9j5pxwdwxs` voice agent.
It does not create, update, or delete the agent. The separate `call_agent.py`
script is available for a one-off text test.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
az login
```

The signed-in Azure identity must have permission to use the Foundry project.

## Send a message

```powershell
python .\call_agent.py --message "Hello! Please briefly introduce yourself."
```

Omit `--message` to be prompted interactively. The project endpoint and agent
name are set near the top of `call_agent.py`.

## Run the voice-first browser app

The app streams microphone audio and plays the agent's spoken replies. Add and
share up to five JPEG, PNG, or WebP images, then ask about them aloud; images
are visual context in that same voice session, not text-chat prompts. Images
require an image-capable model configured on the existing agent.

```powershell
python -m pip install -r .\requirements.txt
az login
python .\webapp.py
```

Open [http://127.0.0.1:8780](http://127.0.0.1:8780) and allow microphone
access when prompted. The voice session starts automatically; speak naturally
and the agent responds aloud. If browser audio policy prevents automatic
startup, select **Retry**. To ask about an image, choose **Add image**, select
**Share with agent**, then ask your question by voice. Use a headset to reduce
echo.

## Deploy to Azure App Service

Use the Linux Python 3.11 runtime. The app binds to `0.0.0.0` and uses the
App Service `PORT` (defaulting to 8000 there); locally it remains bound to
localhost. The GitHub Actions workflow deploys this repository to the
`BharatBhashaAi` App Service.

Before deploying, configure App Service Authentication with Microsoft Entra ID
and require sign-in. The app rejects unauthenticated App Service requests using
the `X-MS-CLIENT-PRINCIPAL` header; `/healthz` is left available for health
probes. Enable WebSockets for realtime voice. Enable the system-assigned
managed identity and grant it access to the Foundry project.

Set `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_VOICE_AGENT_NAME` as App Service
settings if using values other than the defaults. For a custom domain, add its
hostname to `APP_SERVICE_ALLOWED_HOSTS` as a comma-separated list. The
configured agent's model must accept image input. Image/audio retention follows
the existing agent and Foundry project settings; the app does not change them.
