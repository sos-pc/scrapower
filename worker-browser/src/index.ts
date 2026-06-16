// Scrapower Browser Worker — CPU + GPU compute
import { createUI } from "./ui";
import { hasWebGPU, executeGPU } from "./gpu";

const SANDBOX_CODE = `
// Web Worker sandbox — executes WASM modules.
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

const sandboxURL = URL.createObjectURL(
  new Blob([SANDBOX_CODE], { type: "application/javascript" }),
);
const sandboxWorker = new Worker(sandboxURL);

class BrowserWorker {
  private ws: WebSocket | null = null;
  private sessionId: string = "";
  private ui: ReturnType<typeof createUI>;
  private active: boolean = true;
  private stats = { tasks: 0, cpuMs: 0, dataBytes: 0 };
  private hbInterval: number = 10_000;
  private hbTimer: ReturnType<typeof setInterval> | null = null;
  private url: string;

  constructor(wsUrl: string) {
    this.url = wsUrl;
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

  private async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise<void>((resolve, reject) => {
      this.ws!.onopen = () => resolve();
      this.ws!.onerror = () => reject(new Error("WebSocket connection failed"));
    });
    console.log("[scrapower] connecting to", this.url);

    this.ws.send(
      JSON.stringify({
        type: "hello",
        version: "2.1",
        mode: "persistent",
        worker_id: `browser-${Math.random().toString(36).slice(2, 10)}`,
        auth: { method: "none" },
      }),
    );

    const sessionMsg = await this.receive();
    this.sessionId = sessionMsg.session_id;
    this.hbInterval = sessionMsg.heartbeat_interval_ms || 10_000;
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
            gpu: { supported: hasWebGPU() },
          },
          lifecycle: { mode: "persistent", idle_timeout_sec: 300 },
          verification: { can_challenge: false, challenge_timeout_max_sec: 0 },
          network: { connectivity: "outgoing_only" },
          limits: { max_task_duration_ms: 120_000, max_concurrent_tasks: 1 },
        },
      }),
    );

    this.ws.onmessage = (e) => this.handleMessage(JSON.parse(e.data as string));
    this.ws.onclose = () => {
      this.ui.update({ connected: false });
    };
  }

  private receive(): Promise<any> {
    return new Promise((resolve) => {
      const handler = (e: MessageEvent) => {
        this.ws!.removeEventListener("message", handler);
        resolve(JSON.parse(e.data as string));
      };
      this.ws!.addEventListener("message", handler);
    });
  }

  private heartbeat() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type: "heartbeat",
        session_id: this.sessionId,
        current_load_pct: 0,
        tasks_in_progress: this.stats.tasks,
        uptime_sec: 0,
        expected_remaining_sec: null,
      }),
    );
  }

  private async handleMessage(msg: any) {
    if (!this.active) return;
    if (msg.type === "task_assign" || msg.type === "keepalive") {
      if (msg.type === "task_assign") {
        this.ws!.send(
          JSON.stringify({
            type: "task_accept",
            session_id: this.sessionId,
            task_id: msg.task.id,
            assignment_token: msg.task.assignment_token,
          }),
        );
      }
      console.log("[scrapower] task received:", msg.task.id);
      await this.executeTask(msg.task);
    }
  }

  private async executeTask(task: any) {
    const httpUrl = this.url
      .replace("wss://", "https://")
      .replace("ws://", "http://")
      .replace("/worker/ws", "");

    try {
      console.log(
        "[scrapower] downloading input:",
        task.payload.input_hash.slice(0, 8),
      );
      const inputResp = await fetch(
        `${httpUrl}/blobs/${task.payload.input_hash}`,
      );
      const input = await inputResp.arrayBuffer();

      // ── GPU path ──
      const gpuRequired = task.resources_required?.gpu_required;
      if (gpuRequired && hasWebGPU()) {
        console.log("[scrapower] ⚡ GPU task:", task.id);
        const gpuResult = await executeGPU(new Uint8Array(input));
        await this.submitResult(task, gpuResult.outputBytes, gpuResult);
        return;
      }

      // ── CPU path (WASM sandbox) ──
      console.log(
        "[scrapower] downloading wasm:",
        task.payload.executable_hash.slice(0, 8),
      );
      const execResp = await fetch(
        `${httpUrl}/blobs/${task.payload.executable_hash}`,
      );
      const wasm = await execResp.arrayBuffer();

      const result: any = await new Promise((resolve) => {
        const handler = (e: MessageEvent) => {
          sandboxWorker.removeEventListener("message", handler);
          resolve(e.data);
        };
        sandboxWorker.addEventListener("message", handler);
        sandboxWorker.postMessage({ type: "execute", wasm, input }, [
          wasm,
          input,
        ]);
      });

      await this.submitResult(task, result.outputBytes, result);
    } catch (err: any) {
      console.error("Browser worker: task execution failed", err);
    }
  }

  private async submitResult(task: any, outputBytes: Uint8Array, result: any) {
    const httpUrl = this.url
      .replace("wss://", "https://")
      .replace("ws://", "http://")
      .replace("/worker/ws", "");

    console.log("[scrapower] uploading result");
    const putResp = await fetch(`${httpUrl}/blobs`, {
      method: "PUT",
      body: outputBytes,
    });
    const { hash: outputHash } = await putResp.json();

    console.log(
      "[scrapower] submitting result, status:",
      result.error ? "error" : "success",
    );
    this.ws!.send(
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
            stderr: result.error || "",
          },
        },
        verification_data: null,
      }),
    );

    this.stats.tasks++;
    this.stats.cpuMs += result.durationMs || 0;
    this.stats.dataBytes += outputBytes?.length || 0;
    this.ui.update({
      tasksCompleted: this.stats.tasks,
      cpuTimeSec: this.stats.cpuMs / 1000,
      dataMb: this.stats.dataBytes / (1024 * 1024),
    });
  }

  async disconnect() {
    if (this.hbTimer) clearInterval(this.hbTimer);
    if (this.ws && !this.ws.closed) {
      this.ws.send(
        JSON.stringify({
          type: "bye",
          session_id: this.sessionId,
          reason: "user_disconnect",
        }),
      );
      this.ws.close();
    }
  }
}

const wsUrl =
  (window as any).SCRAPOWER_WS_URL ||
  (location.protocol === "https:"
    ? `wss://${location.host}/worker/ws`
    : `ws://${location.host}/worker/ws`);
const worker = new BrowserWorker(wsUrl);
worker.start();
