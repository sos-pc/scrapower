// src/ui.ts
var CSS = `
#scrapower-widget {
  position: fixed;
  bottom: 16px;
  right: 16px;
  width: 280px;
  background: #1a1a2e;
  color: #e0e0e0;
  border-radius: 12px;
  padding: 16px;
  font-family: system-ui, sans-serif;
  font-size: 13px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  z-index: 99999;
}
#scrapower-widget .status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
#scrapower-widget .dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: #f00; transition: background 0.3s;
}
#scrapower-widget .dot.on { background: #0f0; }
#scrapower-widget .stats { line-height: 1.8; margin-bottom: 12px; }
#scrapower-widget .toggle {
  width: 100%; padding: 8px; border: none; border-radius: 6px;
  cursor: pointer; font-weight: bold; font-size: 14px;
  background: #0f0; color: #000;
}
#scrapower-widget .toggle.off { background: #f00; color: #fff; }
`;
function createUI() {
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  const widget = document.createElement("div");
  widget.id = "scrapower-widget";
  widget.innerHTML = `
    <div class="status">
      <div class="dot off"></div>
      <span class="state-text">D\xE9connect\xE9</span>
    </div>
    <div class="stats">T\xE2ches : <span class="tasks">0</span><br>GPU : <span class="gpu-label"></span><br>CPU : <span class="cpu">0.0</span>s<br>Donn\xE9es : <span class="data">0.0</span> Mo</div>
    <button class="toggle on">\u25CF Actif</button>
  `;
  document.body.appendChild(widget);
  const dot = widget.querySelector(".dot");
  const stateText = widget.querySelector(".state-text");
  const tasksEl = widget.querySelector(".tasks");
  const cpuEl = widget.querySelector(".cpu");
  const dataEl = widget.querySelector(".data");
  const toggleBtn = widget.querySelector(".toggle");
  let active = true;
  let toggleCallback = null;
  const gpuLabel = widget.querySelector(".gpu-label");
  (async () => {
    gpuLabel.textContent = navigator.gpu ? "GPU: WebGPU" : "";
    try {
      const a = await navigator.gpu?.requestAdapter();
      if (a) {
        const i = await a.requestAdapterInfo();
        gpuLabel.textContent = "GPU: " + (i.architecture || "WebGPU");
      }
    } catch {
    }
  })();
  toggleBtn.addEventListener("click", () => {
    active = !active;
    toggleBtn.textContent = active ? "\u25CF Actif" : "\u25CB Inactif";
    toggleBtn.className = `toggle ${active ? "on" : "off"}`;
    if (active) {
      dot.className = "dot on";
      stateText.textContent = "Connect\xE9";
    } else {
      dot.className = "dot";
      stateText.textContent = "En pause";
    }
    toggleCallback?.(active);
  });
  return {
    update(state) {
      if (state.connected !== void 0) {
        dot.className = `dot ${state.connected ? "on" : ""}`;
        stateText.textContent = state.connected ? "Connect\xE9" : "D\xE9connect\xE9";
      }
      if (state.tasksCompleted !== void 0) {
        tasksEl.textContent = String(state.tasksCompleted);
      }
      if (state.cpuTimeSec !== void 0) {
        cpuEl.textContent = state.cpuTimeSec.toFixed(1);
      }
      if (state.dataMb !== void 0) {
        dataEl.textContent = state.dataMb.toFixed(1);
      }
    },
    onToggle(cb) {
      toggleCallback = cb;
    }
  };
}

// src/gpu.ts
function hasWebGPU() {
  return !!navigator.gpu;
}
async function sha256(data) {
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}
var MATMUL_SHADER = `
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
async function executeGPU(inputBytes) {
  const start = performance.now();
  try {
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) throw new Error("No WebGPU adapter");
    const device = await adapter.requestDevice();
    const N = new Uint32Array(inputBytes.slice(0, 4).buffer)[0];
    const floats = new Float32Array(
      inputBytes.slice(4).buffer,
      0,
      (inputBytes.length - 4) / 4
    );
    console.log("[scrapower:gpu] matrix size:", N, "x", N);
    const inputData = new Float32Array(1 + 2 * N * N);
    inputData[0] = N;
    inputData.set(floats, 1);
    const inputBuffer = device.createBuffer({
      size: inputData.byteLength,
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST
    });
    device.queue.writeBuffer(inputBuffer, 0, inputData);
    const outputSize = N * N * 4;
    const outputBuffer = device.createBuffer({
      size: outputSize,
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC
    });
    const shaderModule = device.createShaderModule({ code: MATMUL_SHADER });
    const pipeline = device.createComputePipeline({
      layout: "auto",
      compute: { module: shaderModule, entryPoint: "main" }
    });
    const bindGroup = device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [
        { binding: 0, resource: { buffer: inputBuffer } },
        { binding: 1, resource: { buffer: outputBuffer } }
      ]
    });
    const encoder = device.createCommandEncoder();
    const pass = encoder.beginComputePass();
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.dispatchWorkgroups(Math.ceil(N / 8), Math.ceil(N / 8));
    pass.end();
    const stagingBuffer = device.createBuffer({
      size: outputSize,
      usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ
    });
    encoder.copyBufferToBuffer(outputBuffer, 0, stagingBuffer, 0, outputSize);
    device.queue.submit([encoder.finish()]);
    await stagingBuffer.mapAsync(GPUMapMode.READ);
    const outputData = new Uint8Array(stagingBuffer.getMappedRange());
    const outputBytes = new Uint8Array(outputData);
    stagingBuffer.unmap();
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
      hash.slice(0, 12)
    );
    return { outputHash: hash, outputBytes, durationMs };
  } catch (err) {
    console.error("[scrapower:gpu] error:", err.message || err);
    return {
      outputHash: "",
      outputBytes: new Uint8Array(),
      durationMs: Math.round(performance.now() - start),
      error: err.message || String(err)
    };
  }
}

// src/index.ts
var SANDBOX_CODE = `
// Web Worker sandbox \u2014 executes WASM modules.
console.log("[scrapower:sandbox] ready");

async function sha256(data) {
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

self.onmessage = async function (e) {
  if (e.data.type !== "execute") return;
  const start = performance.now();
  try {
    const wasm = e.data.wasm;
    const input = e.data.input;
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

    self.postMessage({ type: "result", outputHash, outputBytes, durationMs: ms });
  } catch (err) {
    self.postMessage({
      type: "result",
      outputHash: "",
      outputBytes: new Uint8Array(),
      durationMs: Math.round(performance.now() - start),
      error: err.message || String(err),
    });
  }
};
`;
var sandboxURL = URL.createObjectURL(
  new Blob([SANDBOX_CODE], { type: "application/javascript" })
);
var sandboxWorker = new Worker(sandboxURL);
var BrowserWorker = class {
  ws = null;
  sessionId = "";
  ui;
  active = true;
  stats = { tasks: 0, cpuMs: 0, dataBytes: 0 };
  hbInterval = 1e4;
  hbTimer = null;
  url;
  constructor(wsUrl2) {
    this.url = wsUrl2;
    this.ui = createUI();
    this.ui.onToggle((active) => {
      this.active = active;
    });
  }
  async start() {
    await this.connect();
    this.hbTimer = setInterval(() => this.heartbeat(), this.hbInterval);
    this.ui.update({ connected: true });
  }
  async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("WebSocket connection failed"));
    });
    console.log("[scrapower] connecting to", this.url);
    this.ws.send(
      JSON.stringify({
        type: "hello",
        version: "2.1",
        mode: "persistent",
        worker_id: `browser-${Math.random().toString(36).slice(2, 10)}`,
        auth: { method: "none" }
      })
    );
    const sessionMsg = await this.receive();
    this.sessionId = sessionMsg.session_id;
    this.hbInterval = sessionMsg.heartbeat_interval_ms || 1e4;
    console.log("[scrapower] connected, session:", this.sessionId);
    this.ws.send(
      JSON.stringify({
        type: "capabilities",
        session_id: this.sessionId,
        payload: {
          runtimes: ["wasm"],
          resources: {
            cpu_cores: navigator.hardwareConcurrency || 2,
            ram_mb: 4096,
            gpu: { supported: hasWebGPU() }
          },
          lifecycle: { mode: "persistent", idle_timeout_sec: 300 },
          verification: { can_challenge: false, challenge_timeout_max_sec: 0 },
          network: { connectivity: "outgoing_only" },
          limits: { max_task_duration_ms: 12e4, max_concurrent_tasks: 1 }
        }
      })
    );
    this.ws.onmessage = (e) => this.handleMessage(JSON.parse(e.data));
    this.ws.onclose = () => {
      this.ui.update({ connected: false });
    };
  }
  receive() {
    return new Promise((resolve) => {
      const handler = (e) => {
        this.ws.removeEventListener("message", handler);
        resolve(JSON.parse(e.data));
      };
      this.ws.addEventListener("message", handler);
    });
  }
  heartbeat() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type: "heartbeat",
        session_id: this.sessionId,
        current_load_pct: 0,
        tasks_in_progress: this.stats.tasks,
        uptime_sec: 0,
        expected_remaining_sec: null
      })
    );
  }
  async handleMessage(msg) {
    if (!this.active) return;
    if (msg.type === "task_assign" || msg.type === "keepalive") {
      if (msg.type === "task_assign") {
        this.ws.send(
          JSON.stringify({
            type: "task_accept",
            session_id: this.sessionId,
            task_id: msg.task.id,
            assignment_token: msg.task.assignment_token
          })
        );
      }
      console.log("[scrapower] task received:", msg.task.id);
      await this.executeTask(msg.task);
    }
  }
  async executeTask(task) {
    const httpUrl = this.url.replace("wss://", "https://").replace("ws://", "http://").replace("/worker/ws", "");
    try {
      console.log(
        "[scrapower] downloading input:",
        task.payload.input_hash.slice(0, 8)
      );
      const inputResp = await fetch(
        `${httpUrl}/blobs/${task.payload.input_hash}`
      );
      const input = await inputResp.arrayBuffer();
      const gpuRequired = task.resources_required?.gpu_required;
      if (gpuRequired && hasWebGPU()) {
        console.log("[scrapower] \u26A1 GPU task:", task.id);
        const gpuResult = await executeGPU(new Uint8Array(input));
        await this.submitResult(task, gpuResult.outputBytes, gpuResult);
        return;
      }
      console.log(
        "[scrapower] downloading wasm:",
        task.payload.executable_hash.slice(0, 8)
      );
      const execResp = await fetch(
        `${httpUrl}/blobs/${task.payload.executable_hash}`
      );
      const wasm = await execResp.arrayBuffer();
      const result = await new Promise((resolve) => {
        const handler = (e) => {
          sandboxWorker.removeEventListener("message", handler);
          resolve(e.data);
        };
        sandboxWorker.addEventListener("message", handler);
        sandboxWorker.postMessage({ type: "execute", wasm, input }, [
          wasm,
          input
        ]);
      });
      await this.submitResult(task, result.outputBytes, result);
    } catch (err) {
      console.error("Browser worker: task execution failed", err);
    }
  }
  async submitResult(task, outputBytes, result) {
    const httpUrl = this.url.replace("wss://", "https://").replace("ws://", "http://").replace("/worker/ws", "");
    console.log("[scrapower] uploading result");
    const putResp = await fetch(`${httpUrl}/blobs`, {
      method: "PUT",
      body: outputBytes
    });
    const { hash: outputHash } = await putResp.json();
    console.log(
      "[scrapower] submitting result, status:",
      result.error ? "error" : "success"
    );
    this.ws.send(
      JSON.stringify({
        type: "task_result",
        session_id: this.sessionId,
        task_id: task.id,
        assignment_token: task.assignment_token || "",
        status: result.error ? "error" : "success",
        result: {
          output_hash: outputHash,
          execution_metadata: {
            duration_ms: result.durationMs || 0,
            exit_code: result.error ? 1 : 0,
            stderr: result.error || ""
          }
        },
        verification_data: null
      })
    );
    this.stats.tasks++;
    this.stats.cpuMs += result.durationMs || 0;
    this.stats.dataBytes += outputBytes?.length || 0;
    this.ui.update({
      tasksCompleted: this.stats.tasks,
      cpuTimeSec: this.stats.cpuMs / 1e3,
      dataMb: this.stats.dataBytes / (1024 * 1024)
    });
  }
  async disconnect() {
    if (this.hbTimer) clearInterval(this.hbTimer);
    if (this.ws && !this.ws.closed) {
      this.ws.send(
        JSON.stringify({
          type: "bye",
          session_id: this.sessionId,
          reason: "user_disconnect"
        })
      );
      this.ws.close();
    }
  }
};
var wsUrl = window.SCRAPOWER_WS_URL || (location.protocol === "https:" ? `wss://${location.host}/worker/ws` : `ws://${location.host}/worker/ws`);
var worker = new BrowserWorker(wsUrl);
worker.start();
