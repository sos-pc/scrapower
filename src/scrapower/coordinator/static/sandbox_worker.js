async function sha256(data) {
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hashBuffer)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
self.onmessage = async function(e) {
  if (e.data.type !== "execute") return;
  const start = performance.now();
  try {
    const { wasm, input } = e.data;
    const module = await WebAssembly.instantiate(wasm);
    const memory = module.instance.exports.memory;
    const compute = module.instance.exports.compute;
    if (!compute) throw new Error("no compute export");
    const inputBytes = new Uint8Array(input);
    const memView = new Uint8Array(memory.buffer);
    memView.set(inputBytes, 0);
    const outOff = Math.ceil(inputBytes.length / 64) * 64;
    const outSize = 4096;
    compute(0, inputBytes.length, outOff, outSize);
    const outputBytes = new Uint8Array(memView.slice(outOff, outOff + outSize));
    const outputHash = await sha256(outputBytes);
    const ms = Math.round(performance.now() - start);
    const result = { type: "result", outputHash, outputBytes, durationMs: ms };
    self.postMessage(result);
  } catch (err) {
    const result = {
      type: "result",
      outputHash: "",
      outputBytes: new Uint8Array(),
      durationMs: Math.round(performance.now() - start),
      error: err.message || String(err)
    };
    self.postMessage(result);
  }
};
