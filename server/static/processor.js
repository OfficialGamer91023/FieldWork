class PCMProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        // 800 samples = ~50ms of audio at 16kHz
        this.bufferSize = 800;
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferIndex = 0;
    }

    process(inputs, outputs, parameters) {
        const inputChannelData = inputs[0][0];

        if (!inputChannelData) return true;

        for (let i = 0; i < inputChannelData.length; i++) {
            this.buffer[this.bufferIndex++] = inputChannelData[i];

            // When the buffer hits the target size...
            if (this.bufferIndex >= this.bufferSize) {
                // Convert the Float32 buffer to Int16
                const int16Buffer = new Int16Array(this.bufferSize);
                for (let j = 0; j < this.bufferSize; j++) {
                    let s = Math.max(-1, Math.min(1, this.buffer[j]));
                    int16Buffer[j] = s < 0 ? s * 32768 : s * 32767;
                }

                // Send the binary chunk back to the main thread
                this.port.postMessage(int16Buffer.buffer);

                // Reset the buffer
                this.bufferIndex = 0;
            }
        }
        return true; // Keep processor alive
    }
}
registerProcessor('pcm-processor', PCMProcessor);
