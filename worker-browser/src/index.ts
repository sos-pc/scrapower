// Scrapower Browser Worker — CPU (WASM) + GPU (WebGPU) compute.
// Connects to coordinator via WebSocket, receives tasks, executes them.

import { createUI } from "./ui";
import { hasWebGPU, executeGPU } from "./gpu";

// ── Sandbox worker (CPU WASM) ──────────────────────────────
// The sandbox runs in a separate Web Worker so WASM execution
// never blocks the main thread or the WebSocket connection.

const sandboxCode = await fetch(
  new URL("./sandbox_worker.js", import.meta.url),
).then((r) => r.text());

const sandboxBlob = new Blob([sandboxCode], { type: "application/javascript" });
const sandboxWorker = new Worker(URL.createObjectURL(sandboxBlob));

// ── Types ───────────────────────────────────────────────────

interface TaskPayload {
  id: string;
  payload: { executable_hash: string; input_hash: string };
  resources_required?: { gpu_required?: boolean };
  assignment_token?: string;
}

interface ExecutionResult {
  outputBytes: Uint8Array;
  durationMs: number;
  error?: string;
}

// ── BrowserWorker ───────────────────────────────────────────

class BrowserWorker {
  private ws: WebSocket | null = null;
  private sessionId = "";
  private ui = createUI();
  private active = true;
  private stats = { tasks: 0, cpuMs: 0, dataBytes: 0 };
  private hbInterval = 10_000;
  private hbTimer: ReturnType<typeof setInterval> | null = null;
  private coordinatorUrl: string;

  constructor(wsUrl: string) {
    this.coordinatorUrl = wsUrl;
    this.ui.onToggle((active) => {
      this.active = active;
    });
  }

  // ── Lifecycle ──────────────────────────────────────────

  async start() {
    await this.connect();
    this.hbTimer = setInterval(() => this.heartbeat(), this.hbInterval);
    this.ui.update({ connected: true });
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

  // ── Connection ─────────────────────────────────────────

  private async connect() {
    this.ws = new WebSocket(this.coordinatorUrl);
    await new Promise<void>((resolve, reject) => {
      this.ws!.onopen = () => resolve();
      this.ws!.onerror = () => reject(new Error("WebSocket connection failed"));
    });

    const workerId = `browser-${Math.random().toString(36).slice(2, 10)}`;
    console.log("[scrapower] connecting to", this.coordinatorUrl);

    // Handshake
    this.ws.send(
      JSON.stringify({
        type: "hello",
        version: "2.1",
        mode: "persistent",
        worker_id: workerId,
        auth: { method: "none" },
      }),
    );

    const sessionMsg = await this.receiveOnce();
    this.sessionId = sessionMsg.session_id;
    this.hbInterval = sessionMsg.heartbeat_interval_ms || 10_000;
    console.log("[scrapower] connected, session:", this.sessionId);

    // Declare capabilities
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

    this.ws.onmessage = (e) => this.handleMessage(JSON.parse(e.data));
    this.ws.onclose = () => this.ui.update({ connected: false });
  }

  private receiveOnce(): Promise<any> {
    return new Promise((resolve) => {
      const handler = (e: MessageEvent) => {
        this.ws!.removeEventListener("message", handler);
        resolve(JSON.parse(e.data));
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

  // ── Message handling ───────────────────────────────────

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
      await this.executeTask(msg.task);
    }
  }

  // ── Task execution ─────────────────────────────────────

  private async executeTask(task: TaskPayload) {
    const httpUrl = this.httpUrl();
    try {
      // Download input (needed by both CPU and GPU paths)
      const input = await this.downloadBlob(httpUrl, task.payload.input_hash);

      // Route to GPU or CPU
      const gpuRequired = task.resources_required?.gpu_required;
      if (gpuRequired && hasWebGPU()) {
        return await this.executeGpuTask(task, input);
      }
      return await this.executeCpuTask(task, input, httpUrl);
    } catch (err: any) {
      console.error("[scrapower] task execution failed:", err.message || err);
    }
  }

  private async executeGpuTask(task: TaskPayload, input: ArrayBuffer) {
    console.log("[scrapower] ⚡ GPU task:", task.id);
    const result = await executeGPU(new Uint8Array(input));
    await this.submitResult(task, result.outputBytes, result);
  }

  private async executeCpuTask(
    task: TaskPayload,
    input: ArrayBuffer,
    httpUrl: string,
  ) {
    console.log("[scrapower] CPU task:", task.id);
    const wasm = await this.downloadBlob(httpUrl, task.payload.executable_hash);

    const result: ExecutionResult = await new Promise((resolve) => {
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
  }

  // ── Helpers ────────────────────────────────────────────

  private httpUrl(): string {
    return this.coordinatorUrl
      .replace("wss://", "https://")
      .replace("ws://", "http://")
      .replace("/worker/ws", "");
  }

  private async downloadBlob(
    httpUrl: string,
    hash: string,
  ): Promise<ArrayBuffer> {
    console.log("[scrapower] downloading blob:", hash.slice(0, 8));
    const resp = await fetch(`${httpUrl}/blobs/${hash}`);
    return resp.arrayBuffer();
  }

  private async submitResult(
    task: TaskPayload,
    outputBytes: Uint8Array,
    result: ExecutionResult,
  ) {
    const httpUrl = this.httpUrl();

    console.log("[scrapower] uploading result");
    const putResp = await fetch(`${httpUrl}/blobs`, {
      method: "PUT",
      body: outputBytes,
    });
    const { hash: outputHash } = await putResp.json();

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
}

// ── Bootstrap ───────────────────────────────────────────────

const wsUrl =
  (window as any).SCRAPOWER_WS_URL ||
  (location.protocol === "https:"
    ? `wss://${location.host}/worker/ws`
    : `ws://${location.host}/worker/ws`);

const instance = new BrowserWorker(wsUrl);
instance.start();
