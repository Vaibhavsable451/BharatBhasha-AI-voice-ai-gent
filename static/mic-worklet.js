class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samplesPerChunk = 2400;
    this.chunk = new Int16Array(this.samplesPerChunk);
    this.offset = 0;
    this.phase = 0;
    this.step = 24000 / sampleRate;
    this.muted = false;
    this.port.onmessage = (event) => {
      this.muted = event.data && event.data.type === "mute";
    };
  }

  process(inputs, outputs) {
    const input = inputs[0] && inputs[0][0];
    const output = outputs[0] && outputs[0][0];
    if (output) output.fill(0);
    if (!input || this.muted) return true;

    for (let i = 0; i < input.length; i += 1) {
      this.phase += this.step;
      if (this.phase < 1) continue;
      this.phase -= 1;
      const sample = Math.max(-1, Math.min(1, input[i]));
      this.chunk[this.offset] = sample < 0 ? sample * 32768 : sample * 32767;
      this.offset += 1;
      if (this.offset === this.samplesPerChunk) {
        const chunk = this.chunk;
        this.port.postMessage(chunk.buffer, [chunk.buffer]);
        this.chunk = new Int16Array(this.samplesPerChunk);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
