const MAX_IMAGES_PER_SHARE = 3;
const MAX_IMAGES_PER_SESSION = 5;
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const SAMPLE_RATE = 24000;

const connectButton = document.querySelector("#connect-button");
const micButton = document.querySelector("#mic-button");
const micHint = document.querySelector("#mic-hint");
const imageInput = document.querySelector("#image-input");
const imageTray = document.querySelector("#image-tray");
const imageList = document.querySelector("#image-list");
const imageCount = document.querySelector("#image-count");
const sharedTray = document.querySelector("#shared-tray");
const sharedList = document.querySelector("#shared-list");
const sharedCount = document.querySelector("#shared-count");
const shareButton = document.querySelector("#share-images");
const imageHint = document.querySelector("#image-hint");
const connectionState = document.querySelector("#connection-state");
const stateLabel = document.querySelector("#state-label");
const agentState = document.querySelector("#agent-state");
const stageHint = document.querySelector("#stage-hint");
const caption = document.querySelector("#caption");
const voiceStage = document.querySelector(".voice-stage");

let socket = null;
let audioContext = null;
let micStream = null;
let micSource = null;
let captureNode = null;
let muteNode = null;
let sessionReady = false;
let responseActive = false;
let microphoneMuted = false;
let queuedImages = [];
let sharedImageUrls = [];
let pendingImageUrls = null;
let agentTranscript = "";
let nextPlaybackTime = 0;
const audioSources = new Set();

function setConnectionState(label, kind = "") {
  stateLabel.textContent = label;
  connectionState.className = `connection-state ${kind}`.trim();
}

function setAgentState(label, hint, kind = "") {
  agentState.textContent = label;
  stageHint.textContent = hint;
  voiceStage.classList.toggle("speaking", kind === "speaking");
  voiceStage.classList.toggle("listening", kind === "listening");
}

function updateShareButton() {
  shareButton.disabled = (
    !sessionReady
    || responseActive
    || pendingImageUrls !== null
    || queuedImages.length === 0
    || sharedImageUrls.length >= MAX_IMAGES_PER_SESSION
  );
}

function sendPacket(packet) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new Error("Connect to the agent before sharing an image.");
  }
  socket.send(JSON.stringify(packet));
}

function stopPlayback() {
  for (const source of audioSources) {
    try { source.stop(); } catch { /* Already stopped. */ }
  }
  audioSources.clear();
  nextPlaybackTime = audioContext ? audioContext.currentTime : 0;
}

function playPcm(buffer) {
  if (!audioContext || !buffer.byteLength) return;
  const view = new DataView(buffer);
  const samples = new Float32Array(buffer.byteLength / 2);
  for (let i = 0; i < samples.length; i += 1) {
    samples[i] = view.getInt16(i * 2, true) / 32768;
  }
  const audio = audioContext.createBuffer(1, samples.length, SAMPLE_RATE);
  audio.copyToChannel(samples, 0);
  const source = audioContext.createBufferSource();
  source.buffer = audio;
  source.connect(audioContext.destination);
  source.onended = () => audioSources.delete(source);
  if (nextPlaybackTime < audioContext.currentTime) {
    nextPlaybackTime = audioContext.currentTime + 0.035;
  }
  source.start(nextPlaybackTime);
  nextPlaybackTime += audio.duration;
  audioSources.add(source);
}

function handlePacket(packet) {
  if (packet.type === "ready") {
    sessionReady = true;
    setConnectionState("Connected", "connected");
    connectButton.textContent = "Disconnect";
    connectButton.disabled = false;
    micButton.disabled = false;
    micHint.textContent = "Microphone is live";
    imageHint.textContent = "Share a photo, then ask about it by voice. Images become conversation context.";
    setAgentState("Listening", "Speak naturally. The agent will answer out loud.", "listening");
    updateShareButton();
  } else if (packet.type === "response_start") {
    responseActive = true;
    agentTranscript = "";
    setAgentState("Thinking", "Listening to your question.", "");
    updateShareButton();
  } else if (packet.type === "interrupt") {
    stopPlayback();
    setAgentState("Listening", "Go ahead. The agent is listening.", "listening");
  } else if (packet.type === "transcript" && packet.role === "user") {
    caption.textContent = `You: ${packet.text || ""}`;
  } else if (packet.type === "assistant_delta") {
    agentTranscript += packet.text || "";
    caption.textContent = `BharatBhasha AI: ${agentTranscript}`;
  } else if (packet.type === "assistant_final") {
    agentTranscript = packet.text || "";
    caption.textContent = `BharatBhasha AI: ${agentTranscript}`;
  } else if (packet.type === "done") {
    responseActive = false;
    setAgentState("Listening", "Ask another question whenever you're ready.", "listening");
    updateShareButton();
  } else if (packet.type === "image_context_ready") {
    if (pendingImageUrls) sharedImageUrls.push(...pendingImageUrls);
    pendingImageUrls = null;
    queuedImages = [];
    renderImageTray();
    renderSharedImages();
    caption.textContent = "Image shared. Ask your question aloud.";
    setAgentState("Image ready", "Your agent has the image. Ask about it by voice.", "listening");
    updateShareButton();
  } else if (packet.type === "notice") {
    responseActive = true;
    pendingImageUrls = null;
    caption.textContent = packet.message || "The agent is finishing its current reply.";
    setAgentState("One moment", "Wait for the current spoken response to finish.", "");
    updateShareButton();
  } else if (packet.type === "error") {
    pendingImageUrls = null;
    responseActive = false;
    caption.textContent = packet.message || "The voice session reported an error.";
    setAgentState("Connection issue", "Check the local server and Azure sign-in.", "");
    setConnectionState("Connection issue");
    updateShareButton();
  }
}

function makeSocketUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws`;
}

async function resumeAudioContext(context) {
  let timeoutId;
  await Promise.race([
    context.resume(),
    new Promise((_, reject) => {
      timeoutId = setTimeout(
        () => reject(new Error("Browser audio needs a click. Select Retry to start the voice agent.")),
        3000,
      );
    }),
  ]).finally(() => clearTimeout(timeoutId));
}

async function connect() {
  connectButton.disabled = true;
  connectButton.textContent = "Starting…";
  setConnectionState("Connecting…", "connecting");
  setAgentState("Starting voice session", "Allow microphone access to talk to your agent.");
  try {
    audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
    await audioContext.audioWorklet.addModule("/static/mic-worklet.js");
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    await resumeAudioContext(audioContext);

    socket = new WebSocket(makeSocketUrl());
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        try {
          handlePacket(JSON.parse(event.data));
        } catch (error) {
          caption.textContent = `Could not read the voice-session response: ${error.message}`;
        }
      } else {
        setAgentState("Speaking", "The agent is replying.", "speaking");
        playPcm(event.data);
      }
    };
    socket.onerror = () => {
      caption.textContent = "The local server connection failed. Check that webapp.py is running.";
      setConnectionState("Connection issue");
    };
    socket.onclose = () => {
      disconnectAudio();
      socket = null;
      sessionReady = false;
      responseActive = false;
      pendingImageUrls = null;
      connectButton.textContent = "Connect";
      connectButton.disabled = false;
      micButton.disabled = true;
      micButton.classList.remove("active");
      micButton.title = "Mute microphone";
      micHint.textContent = "Connect to start the microphone";
      microphoneMuted = false;
      setConnectionState("Not connected");
      setAgentState("Ready to listen", "Connect, then speak. The agent will answer out loud.");
      updateShareButton();
    };

    await new Promise((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new Error("Timed out connecting to the local server.")),
        15000,
      );
      socket.addEventListener("open", () => { clearTimeout(timeout); resolve(); }, { once: true });
      socket.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error("Could not open the local server socket."));
      }, { once: true });
    });

    micSource = audioContext.createMediaStreamSource(micStream);
    captureNode = new AudioWorkletNode(audioContext, "pcm-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
      outputChannelCount: [1],
    });
    muteNode = audioContext.createGain();
    muteNode.gain.value = 0;
    captureNode.port.onmessage = (event) => {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(event.data);
    };
    micSource.connect(captureNode);
    captureNode.connect(muteNode);
    muteNode.connect(audioContext.destination);
  } catch (error) {
    caption.textContent = error.name === "NotAllowedError"
      ? "Allow microphone access, then select Retry to start the voice agent."
      : error.message || "Could not start the microphone.";
    disconnectAudio();
    if (socket) socket.close();
    socket = null;
    connectButton.disabled = false;
    connectButton.textContent = "Retry";
    setConnectionState("Not connected");
    setAgentState("Microphone needed", "Allow microphone access, then retry the voice session.");
  }
}

function disconnectAudio() {
  stopPlayback();
  if (captureNode) captureNode.disconnect();
  if (micSource) micSource.disconnect();
  if (muteNode) muteNode.disconnect();
  if (micStream) micStream.getTracks().forEach((track) => track.stop());
  if (audioContext && audioContext.state !== "closed") audioContext.close();
  captureNode = null;
  micSource = null;
  muteNode = null;
  micStream = null;
  audioContext = null;
}

function disconnect() {
  if (socket) socket.close();
  else disconnectAudio();
}

function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

function renderImageTray() {
  imageList.replaceChildren();
  imageTray.hidden = queuedImages.length === 0;
  imageCount.textContent = queuedImages.length
    ? `· ${queuedImages.length}/${MAX_IMAGES_PER_SHARE}`
    : "";
  queuedImages.forEach((file, index) => {
    const chip = document.createElement("div");
    chip.className = "image-chip";
    const image = document.createElement("img");
    image.src = URL.createObjectURL(file);
    image.alt = file.name;
    image.onload = () => URL.revokeObjectURL(image.src);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.addEventListener("click", () => {
      queuedImages.splice(index, 1);
      renderImageTray();
      updateShareButton();
    });
    chip.append(image, remove);
    imageList.append(chip);
  });
  updateShareButton();
}

function renderSharedImages() {
  sharedList.replaceChildren();
  sharedTray.hidden = sharedImageUrls.length === 0;
  sharedCount.textContent = `${sharedImageUrls.length}/${MAX_IMAGES_PER_SESSION}`;
  for (const [index, url] of sharedImageUrls.entries()) {
    const chip = document.createElement("div");
    chip.className = "image-chip";
    const image = document.createElement("img");
    image.src = url;
    image.alt = `Image ${index + 1} shared with the agent`;
    chip.append(image);
    sharedList.append(chip);
  }
}

imageInput.addEventListener("change", () => {
  const chosen = Array.from(imageInput.files || []);
  imageInput.value = "";
  const available = Math.min(
    MAX_IMAGES_PER_SHARE - queuedImages.length,
    MAX_IMAGES_PER_SESSION - sharedImageUrls.length - queuedImages.length,
  );
  if (chosen.length > available) {
    caption.textContent = `You can share up to ${MAX_IMAGES_PER_SESSION} images in one session.`;
  }
  for (const file of chosen.slice(0, Math.max(0, available))) {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      caption.textContent = `${file.name}: choose a JPEG, PNG, or WebP image.`;
    } else if (file.size > MAX_IMAGE_BYTES) {
      caption.textContent = `${file.name}: each image must be 4 MB or smaller.`;
    } else {
      queuedImages.push(file);
    }
  }
  renderImageTray();
});

shareButton.addEventListener("click", async () => {
  if (!sessionReady || responseActive || !queuedImages.length) return;
  try {
    pendingImageUrls = await Promise.all(queuedImages.map(fileAsDataUrl));
    sendPacket({ type: "images", images: pendingImageUrls });
    caption.textContent = "Sharing the image with your agent…";
    setAgentState("Sharing image", "Your agent is receiving visual context.");
    updateShareButton();
  } catch (error) {
    pendingImageUrls = null;
    caption.textContent = error.message;
    setAgentState("Image not shared", "Try choosing a supported image format.");
    updateShareButton();
  }
});

connectButton.addEventListener("click", () => {
  if (socket) disconnect();
  else connect();
});

micButton.addEventListener("click", () => {
  microphoneMuted = !microphoneMuted;
  micButton.classList.toggle("active", microphoneMuted);
  micButton.title = microphoneMuted ? "Unmute microphone" : "Mute microphone";
  micButton.setAttribute("aria-label", micButton.title);
  micHint.textContent = microphoneMuted ? "Microphone muted" : "Microphone is live";
  if (captureNode) {
    captureNode.port.postMessage({ type: microphoneMuted ? "mute" : "unmute" });
  }
});

connect();
