// Mic capture worklet: turns the browser's Float32 audio into 16 kHz Int16 PCM in 50 ms
// frames (800 samples), which is what the orchestrator and AssemblyAI expect.
// If the AudioContext couldn't run at 16 kHz (Firefox), it averages down from the
// context's real rate (`sampleRate` is a global inside worklets).
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frameSize = 800; // 50 ms at 16 kHz
    this.ratio = sampleRate / 16000;
    this.frame = new Int16Array(this.frameSize);
    this.index = 0;
    this.acc = 0;
    this.accCount = 0;
    this.pos = 0;
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;

    for (let i = 0; i < input.length; i++) {
      this.acc += input[i];
      this.accCount++;
      this.pos += 1;
      if (this.pos < this.ratio) continue;
      this.pos -= this.ratio;

      const s = Math.max(-1, Math.min(1, this.acc / this.accCount));
      this.acc = 0;
      this.accCount = 0;
      this.frame[this.index++] = s < 0 ? s * 32768 : s * 32767;

      if (this.index === this.frameSize) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(this.frameSize);
        this.index = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-processor", PCMProcessor);
