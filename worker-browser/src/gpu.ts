// WebGPU compute sandbox for browser workers.
// Executes WGSL compute shaders on the visitor's GPU.

interface GPUResult {
  outputHash: string;
  outputBytes: Uint8Array;
  durationMs: number;
  error?: string;
}

// Check if WebGPU is available
export function hasWebGPU(): boolean {
  return !!(navigator as any).gpu;
}

async function sha256(data: Uint8Array): Promise<string> {
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Matrix multiplication WGSL shader (A × B = C)
// Input layout: matrix_size (4 bytes LE) + A (row-major f32) + B (row-major f32)
// Output: C (row-major f32)
const MATMUL_SHADER = `
struct Matrix {
  size: u32,
  data: array<f32>,
};

@group(0) @binding(0) var<storage, read> input: array<f32>;
@group(0) @binding(1) var<storage, read_write> output: array<f32>;

@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let N = u32(input[0]);  // matrix size (square)
  let row = gid.x;
  let col = gid.y;
  if (row >= N || col >= N) { return; }

  // A starts at index 1, B starts at 1 + N*N
  let a_off = 1u;
  let b_off = 1u + N * N;
  let c_off = 0u;

  var sum: f32 = 0.0;
  for (var k = 0u; k < N; k = k + 1u) {
    sum = sum + input[a_off + row * N + k] * input[b_off + k * N + col];
  }
  output[row * N + col] = sum;
}
`;

export async function executeGPU(inputBytes: Uint8Array): Promise<GPUResult> {
  const start = performance.now();

  try {
    const adapter = await (navigator as any).gpu.requestAdapter();
    if (!adapter) throw new Error("No WebGPU adapter");

    const device = await adapter.requestDevice();

    // Parse input: first 4 bytes = matrix size (u32 LE), then matrix data
    const N = new Uint32Array(inputBytes.slice(0, 4).buffer)[0];
    const floats = new Float32Array(
      inputBytes.slice(4).buffer,
      0,
      (inputBytes.length - 4) / 4,
    );

    console.log("[scrapower:gpu] matrix size:", N, "x", N);

    // Input buffer (size float + two N×N matrices)
    const inputData = new Float32Array(1 + 2 * N * N);
    inputData[0] = N;
    inputData.set(floats, 1);

    const inputBuffer = device.createBuffer({
      size: inputData.byteLength,
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
    });
    device.queue.writeBuffer(inputBuffer, 0, inputData);

    const outputSize = N * N * 4; // f32 = 4 bytes
    const outputBuffer = device.createBuffer({
      size: outputSize,
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC,
    });

    const shaderModule = device.createShaderModule({ code: MATMUL_SHADER });
    const pipeline = device.createComputePipeline({
      layout: "auto",
      compute: { module: shaderModule, entryPoint: "main" },
    });

    const bindGroup = device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [
        { binding: 0, resource: { buffer: inputBuffer } },
        { binding: 1, resource: { buffer: outputBuffer } },
      ],
    });

    const encoder = device.createCommandEncoder();
    const pass = encoder.beginComputePass();
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.dispatchWorkgroups(Math.ceil(N / 8), Math.ceil(N / 8));
    pass.end();

    // Read back results
    const stagingBuffer = device.createBuffer({
      size: outputSize,
      usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
    });
    encoder.copyBufferToBuffer(outputBuffer, 0, stagingBuffer, 0, outputSize);

    device.queue.submit([encoder.finish()]);
    await stagingBuffer.mapAsync(GPUMapMode.READ);
    const outputData = new Uint8Array(stagingBuffer.getMappedRange());
    const outputBytes = new Uint8Array(outputData);
    stagingBuffer.unmap();

    // Verify: compute C[0][0] on CPU for sanity check
    const resultFloats = new Float32Array(outputBytes.buffer);
    let sample = "";
    if (N >= 2) {
      let sum = 0;
      const aOff = 1;
      const bOff = 1 + N * N;
      for (let k = 0; k < N; k++) {
        sum += inputData[aOff + k] * inputData[bOff + k * N];
      }
      const match = Math.abs(resultFloats[0] - sum) < 0.01 ? "OK" : "MISMATCH";
      sample = `C[0][0] GPU=${resultFloats[0].toFixed(4)} CPU=${sum.toFixed(4)} ${match}`;
    }

    const hash = await sha256(outputBytes);
    const durationMs = Math.round(performance.now() - start);

    console.log(
      "[scrapower:gpu] done in",
      durationMs + "ms",
      "|",
      sample,
      "| hash:",
      hash.slice(0, 12),
    );

    return { outputHash: hash, outputBytes, durationMs };
  } catch (err: any) {
    console.error("[scrapower:gpu] error:", err.message || err);
    return {
      outputHash: "",
      outputBytes: new Uint8Array(),
      durationMs: Math.round(performance.now() - start),
      error: err.message || String(err),
    };
  }
}
