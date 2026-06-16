// Scrapower Browser Worker — CPU (WASM) + GPU (WebGPU) compute.
// Connects to coordinator via WebSocket, receives tasks, executes them.

import { createUI } from "./ui";
import { hasWebGPU, executeGPU } from "./gpu";
import { P2PTransport } from "./p2p";
import { DHT } from "./dht";
import { GossipSub } from "./gossip";

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
  runtime?: string;
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
  private pyodide: any = null;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private p2p: P2PTransport | null = null;
  private dht: DHT | null = null;
  private gossip: GossipSub | null = null;
  private workerId = "";

  constructor(wsUrl: string) {
    this.coordinatorUrl = wsUrl;
    this.ui.onToggle((active) => {
      this.active = active;
    });
  }

  // ── Lifecycle ──────────────────────────────────────────

  async start() {
    // Listen for visibility changes (tab hidden = reduce work)
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        console.log("[scrapower] tab hidden, pausing UI updates");
      } else {
        console.log("[scrapower] tab visible");
      }
    });

    await this.connect();
    this.hbTimer = setInterval(() => this.heartbeat(), this.hbInterval);
    this.ui.update({ connected: true });
  }

  async disconnect() {
    this.active = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
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
    this.workerId = workerId;
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
          runtimes: ["wasm", "python"],
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
    this.ws.onclose = () => {
      this.p2p?.disconnect();
      this.p2p = null;
      this.reconnectAttempts = 0;
      if (this.active) {
        this.ui.update({ connected: false });
        this.scheduleReconnect();
      }
    };
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

  private scheduleReconnect() {
    if (!this.active || this.reconnectTimer) return;
    // Exponential backoff: 2s, 4s, 8s, ..., max 60s
    const delay = Math.min(2000 * Math.pow(2, this.reconnectAttempts), 60000);
    console.log(
      `[scrapower] reconnecting in ${delay / 1000}s (attempt ${this.reconnectAttempts + 1})`,
    );
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      this.reconnectAttempts++;
      try {
        await this.connect();
        this.reconnectAttempts = 0;
        this.ui.update({ connected: true });
        console.log("[scrapower] reconnected");
      } catch (err: any) {
        console.error("[scrapower] reconnect failed:", err.message || err);
        this.scheduleReconnect();
      }
    }, delay);
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

    // Relay P2P signalling messages
    if (msg.type?.startsWith("p2p_")) {
      if (!this.p2p) {
        this.p2p = new P2PTransport(this.ws!, this.workerId, (p2pMsg) => {
          if (p2pMsg.type === "p2p_blob_request") {
            this.handleP2PBlobRequest(p2pMsg);
          }
        });
        this.dht = new DHT(this.ws!, this.workerId, this.p2p);
        this.dht
          .init()
          .catch((e) => console.warn("[scrapower:dht] init failed:", e));
        this.gossip = new GossipSub(
          this.workerId,
          this.dht,
          async (peerId, gMsg) => {
            if (this.p2p) {
              const channel = await this.p2p.connectTo(peerId);
              if (channel) channel.send(JSON.stringify(gMsg));
            }
          },
        );
        this.gossip.on("blob_available", (gMsg) => {
          console.log(
            "[scrapower:gossip] blob",
            gMsg.data.blobHash?.slice(0, 8),
            "from",
            gMsg.from,
          );
        });
        this.gossip
          .start()
          .catch((e) => console.warn("[scrapower:gossip] start failed:", e));
      }
      // Route gossip messages
      if (msg.type === "p2p_blob_response" && msg.data?.gossipMsg) {
        this.gossip?.handleMessage(msg.data.gossipMsg);
        return;
      }
      this.p2p.handleMessage(msg);
      return;
    }

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
      const input = await this.downloadBlob(httpUrl, task.payload.input_hash);

      // Route to Python runtime
      if (task.runtime === "python") {
        return await this.executePythonTask(task, input);
      }

      // Route to GPU or CPU
      const gpuRequired =
        task.gpu_required || task.resources_required?.gpu_required;
      if (gpuRequired && hasWebGPU()) {
        return await this.executeGpuTask(task, input);
      }
      return await this.executeCpuTask(task, input, httpUrl);
    } catch (err: any) {
      console.error("[scrapower] task execution failed:", err.message || err);
    }
  }

  private async executePythonTask(task: TaskPayload, input: ArrayBuffer) {
    console.log("[scrapower] 🐍 Python task:", task.id);
    const start = performance.now();

    // Lazy-load Pyodide on first Python task
    if (!this.pyodide) {
      console.log("[scrapower] loading Pyodide...");
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
      await new Promise<void>((resolve, reject) => {
        script.onload = () => resolve();
        script.onerror = () => reject(new Error("Failed to load Pyodide"));
        document.head.appendChild(script);
      });
      this.pyodide = await (window as any).loadPyodide();
      console.log("[scrapower] Pyodide ready");
    }

    try {
      const code = new TextDecoder().decode(input);
      // Capture stdout
      let output = "";
      this.pyodide.setStdout({
        batched: (text: string) => {
          output += text + "\n";
        },
      });
      await this.pyodide.runPythonAsync(code);
      const durationMs = Math.round(performance.now() - start);
      const outputBytes = new TextEncoder().encode(output || "OK");
      await this.submitResult(task, outputBytes, { outputBytes, durationMs });
    } catch (err: any) {
      const durationMs = Math.round(performance.now() - start);
      const outputBytes = new TextEncoder().encode(err.message || String(err));
      await this.submitResult(task, outputBytes, {
        outputBytes,
        durationMs,
        error: err.message,
      });
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

    // Try P2P first
    if (this.p2p) {
      try {
        const peers = await this.p2p.findBlobPeers(hash);
        if (peers.length > 0) {
          console.log("[scrapower] P2P blob from", peers[0]);
          return await this.p2p.requestBlob(peers[0], hash);
        }
      } catch (err) {
        console.warn("[scrapower] P2P failed, falling back to HTTP:", err);
      }
    }

    // Fallback to coordinator HTTP
    const resp = await fetch(`${httpUrl}/blobs/${hash}`);
    const data = await resp.arrayBuffer();

    // Announce to mesh that we have this blob
    this.gossip
      ?.broadcast("blob_available", { blobHash: hash })
      .catch(() => {});

    return data;
  }

  private async handleP2PBlobRequest(msg: any) {
    const { blobHash, requestId, channel } = msg.data || {};
    console.log("[scrapower] P2P blob request for", blobHash?.slice(0, 8));
    try {
      const httpUrl = this.httpUrl();
      const resp = await fetch(`${httpUrl}/blobs/${blobHash}`);
      const data = await resp.arrayBuffer();
      this.p2p!.sendBlobResponse(channel, requestId, data);
    } catch (err) {
      console.error("[scrapower] P2P blob relay failed:", err);
    }
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

// Register Service Worker for caching
if ("serviceWorker" in navigator) {
  navigator.serviceWorker
    .register("/sw.js", { scope: "/" })
    .catch((err) => console.warn("[scrapower] SW registration failed:", err));
}

const wsUrl =
  (window as any).SCRAPOWER_WS_URL ||
  (location.protocol === "https:"
    ? `wss://${location.host}/worker/ws`
    : `ws://${location.host}/worker/ws`);

const instance = new BrowserWorker(wsUrl);
instance.start();
