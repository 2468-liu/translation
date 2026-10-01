class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.chunkSamples = 1600;
    this.ratio = sampleRate / this.targetRate;
    this.acc = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(this.chunkSamples);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i += 1) {
      this.sum += channel[i];
      this.count += 1;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        const avg = this.sum / Math.max(1, this.count);
        const clipped = Math.max(-1, Math.min(1, avg));
        this.out[this.filled] = (clipped * 32767) | 0;
        this.filled += 1;
        this.acc -= this.ratio;
        this.sum = 0;
        this.count = 0;
        if (this.filled >= this.chunkSamples) {
          const buffer = this.out.buffer;
          this.port.postMessage(buffer, [buffer]);
          this.out = new Int16Array(this.chunkSamples);
          this.filled = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-processor", PcmProcessor);
