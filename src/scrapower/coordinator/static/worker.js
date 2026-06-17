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

// src/p2p.ts
var ICE_SERVERS = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
};
var P2PTransport = class {
  ws;
  workerId;
  peers = /* @__PURE__ */ new Map();
  blobCallbacks = /* @__PURE__ */ new Map();
  onSignal;
  constructor(ws, workerId, onSignal) {
    this.ws = ws;
    this.workerId = workerId;
    this.onSignal = onSignal;
  }
  async handleMessage(msg) {
    if (msg.to !== this.workerId) return;
    switch (msg.type) {
      case "p2p_offer":
        await this.handleOffer(msg);
        break;
      case "p2p_answer":
        await this.handleAnswer(msg);
        break;
      case "p2p_ice":
        await this.handleIce(msg);
        break;
      case "p2p_blob_response":
        this.handleBlobResponse(msg);
        break;
    }
  }
  // Request a blob from a peer
  async requestBlob(peerWorkerId, blobHash) {
    const peer = this.peers.get(peerWorkerId);
    if (peer?.channel?.readyState === "open")
      return this.sendBlobRequest(peer, blobHash);
    return this.connectAndRequest(peerWorkerId, blobHash);
  }
  // Connect to peer (for DHT ping), returns channel or null
  async connectTo(peerWorkerId) {
    const existing = this.peers.get(peerWorkerId);
    if (existing?.channel?.readyState === "open") return existing.channel;
    try {
      await this.connectAndRequest(peerWorkerId, "__ping__");
      return this.peers.get(peerWorkerId)?.channel || null;
    } catch {
      return null;
    }
  }
  // Find which peers have a blob (via coordinator)
  async findBlobPeers(blobHash) {
    return new Promise((resolve) => {
      const rid = `find_${Math.random().toString(36).slice(2)}`;
      this.ws.send(
        JSON.stringify({
          type: "p2p_blob_request",
          from: this.workerId,
          to: "",
          data: { blobHash, requestId: rid }
        })
      );
      const h = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "p2p_blob_peers" && m.requestId === rid) {
          this.ws.removeEventListener("message", h);
          resolve(m.peers || []);
        }
      };
      this.ws.addEventListener("message", h);
      setTimeout(() => {
        this.ws.removeEventListener("message", h);
        resolve([]);
      }, 5e3);
    });
  }
  async connectAndRequest(peerWorkerId, blobHash) {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    const channel = pc.createDataChannel("scrapower-blob");
    const peer = { pc, channel, workerId: peerWorkerId };
    this.peers.set(peerWorkerId, peer);
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("P2P timeout")), 15e3);
      channel.onopen = async () => {
        clearTimeout(timeout);
        if (blobHash === "__ping__") {
          resolve(new ArrayBuffer(0));
          return;
        }
        try {
          resolve(await this.sendBlobRequest(peer, blobHash));
        } catch (e) {
          reject(e);
        }
      };
      pc.onicecandidate = (e) => {
        if (e.candidate)
          this.sendSignal({
            type: "p2p_ice",
            from: this.workerId,
            to: peerWorkerId,
            data: e.candidate
          });
      };
      pc.createOffer().then((offer) => {
        pc.setLocalDescription(offer);
        this.sendSignal({
          type: "p2p_offer",
          from: this.workerId,
          to: peerWorkerId,
          data: offer
        });
      }).catch(reject);
    });
  }
  async sendBlobRequest(peer, blobHash) {
    const rid = Math.random().toString(36).slice(2, 10);
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("Blob timeout")), 3e4);
      this.blobCallbacks.set(rid, (d) => {
        clearTimeout(t);
        resolve(d);
      });
      peer.channel.send(
        JSON.stringify({ type: "blob_request", blobHash, requestId: rid })
      );
    });
  }
  sendBlobResponse(channel, requestId, data) {
    const bytes = new Uint8Array(data);
    const CHUNK = 16384;
    const total = Math.ceil(bytes.length / CHUNK);
    channel.send(
      JSON.stringify({
        type: "blob_response_start",
        requestId,
        totalChunks: total,
        totalSize: bytes.length
      })
    );
    for (let i = 0; i < total; i++)
      channel.send(bytes.slice(i * CHUNK, (i + 1) * CHUNK));
  }
  async handleOffer(msg) {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    const peer = { pc, channel: null, workerId: msg.from };
    this.peers.set(msg.from, peer);
    pc.ondatachannel = (e) => {
      peer.channel = e.channel;
      this.setupIncoming(e.channel);
    };
    pc.onicecandidate = (e) => {
      if (e.candidate)
        this.sendSignal({
          type: "p2p_ice",
          from: this.workerId,
          to: msg.from,
          data: e.candidate
        });
    };
    await pc.setRemoteDescription(new RTCSessionDescription(msg.data));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    this.sendSignal({
      type: "p2p_answer",
      from: this.workerId,
      to: msg.from,
      data: answer
    });
  }
  async handleAnswer(msg) {
    const peer = this.peers.get(msg.from);
    if (peer)
      await peer.pc.setRemoteDescription(new RTCSessionDescription(msg.data));
  }
  async handleIce(msg) {
    const peer = this.peers.get(msg.from);
    if (peer?.data)
      await peer.pc.addIceCandidate(new RTCIceCandidate(msg.data));
  }
  handleBlobResponse(msg) {
    if (msg.data?.blobData) {
      const cb = this.blobCallbacks.get(msg.data.requestId);
      if (cb) {
        cb(new Uint8Array(msg.data.blobData).buffer);
        this.blobCallbacks.delete(msg.data.requestId);
      }
    }
  }
  setupIncoming(channel) {
    const chunks = /* @__PURE__ */ new Map();
    channel.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data);
        if (m.type === "blob_request")
          this.onSignal({
            type: "p2p_blob_request",
            from: "",
            to: this.workerId,
            data: m
          });
        else if (m.type === "blob_response_start")
          chunks.set(m.requestId, {
            total: m.totalChunks,
            data: [],
            size: m.totalSize
          });
      } else if (e.data instanceof ArrayBuffer) {
        for (const [rid, s] of chunks) {
          if (s.data.length < s.total) {
            s.data.push(new Uint8Array(e.data));
            if (s.data.length === s.total) {
              const r = new Uint8Array(s.size);
              let o = 0;
              for (const c of s.data) {
                r.set(c, o);
                o += c.length;
              }
              chunks.delete(rid);
              const cb = this.blobCallbacks.get(rid);
              if (cb) {
                cb(r.buffer);
                this.blobCallbacks.delete(rid);
              }
            }
            break;
          }
        }
      }
    };
  }
  sendSignal(msg) {
    if (this.ws.readyState === WebSocket.OPEN)
      this.ws.send(JSON.stringify(msg));
  }
  disconnect() {
    for (const p of this.peers.values()) {
      p.channel?.close();
      p.pc.close();
    }
    this.peers.clear();
  }
  get peerCount() {
    return this.peers.size;
  }
};

// src/dht.ts
var K = 8;
var ALPHA = 3;
async function sha256Hex(input) {
  const hashBuffer = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(input)
  );
  return Array.from(new Uint8Array(hashBuffer)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
function xorDistance(a, b) {
  return BigInt("0x" + a) ^ BigInt("0x" + b);
}
var DHT = class {
  nodeId;
  workerId;
  p2p;
  routingTable = [];
  // sorted by XOR distance from own node ID
  _store = /* @__PURE__ */ new Map();
  ws;
  constructor(ws, workerId, p2p) {
    this.ws = ws;
    this.workerId = workerId;
    this.p2p = p2p;
    this.nodeId = "";
  }
  async init() {
    this.nodeId = await sha256Hex(this.workerId);
    await this.bootstrap();
    console.log(
      "[scrapower:dht] initialized, nodeId:",
      this.nodeId.slice(0, 12)
    );
  }
  // Bootstrap: ask coordinator for list of active workers, then ping them
  async bootstrap() {
    const peers = await this.requestPeerList();
    console.log("[scrapower:dht] bootstrap: found", peers.length, "peers");
    for (const peerId of peers.slice(0, K * 2)) {
      if (peerId === this.workerId) continue;
      await this.ping(peerId);
    }
  }
  // Find the K closest nodes to a target (Kademlia FIND_NODE)
  async findNode(targetId) {
    const candidates = [...this.routingTable].sort(
      (a, b) => Number(xorDistance(a.id, targetId) - xorDistance(b.id, targetId))
    );
    return candidates.slice(0, K);
  }
  // Store a key-value pair on the K closest nodes
  async store(key, value) {
    const targetId = await sha256Hex(key);
    const closest = await this.findNode(targetId);
    this._store.set(key, { key, value, timestamp: Date.now() });
    for (const node of closest) {
      try {
        await this.p2p.requestBlob(node.workerId, key);
      } catch {
      }
    }
  }
  // Find a value in the DHT
  async findValue(key) {
    const local = this._store.get(key);
    if (local) return local.value;
    const targetId = await sha256Hex(key);
    const closest = await this.findNode(targetId);
    for (const node of closest.slice(0, ALPHA)) {
      try {
        const data = await this.p2p.requestBlob(node.workerId, `dht:${key}`);
        if (data) return new TextDecoder().decode(data);
      } catch {
        continue;
      }
    }
    return null;
  }
  // Advertise that we have a blob
  async advertiseBlob(blobHash) {
    await this.store(`blob:${blobHash}`, this.workerId);
  }
  // Find which workers have a blob
  async findBlobWorkers(blobHash) {
    const value = await this.findValue(`blob:${blobHash}`);
    if (value) return [value];
    return this.findBlobPeersFromCoordinator(blobHash);
  }
  // Ping a peer and add to routing table
  async ping(peerWorkerId) {
    const peerNodeId = await sha256Hex(peerWorkerId);
    const existing = this.routingTable.find((n) => n.id === peerNodeId);
    if (existing) return;
    try {
      const channel = await this.p2p.connectTo(peerWorkerId);
      if (channel) {
        this.routingTable.push({ id: peerNodeId, workerId: peerWorkerId });
        this.routingTable.sort(
          (a, b) => Number(
            xorDistance(a.id, this.nodeId) - xorDistance(b.id, this.nodeId)
          )
        );
        if (this.routingTable.length > K * 20) {
          this.routingTable = this.routingTable.slice(0, K * 20);
        }
      }
    } catch {
    }
  }
  // Remove stale entries
  prune() {
    const now = Date.now();
    this._store.forEach((entry, key) => {
      if (now - entry.timestamp > 36e5) {
        this._store.delete(key);
      }
    });
  }
  // Get peer list from coordinator
  async requestPeerList() {
    return new Promise((resolve) => {
      const requestId = `dht_peers_${Math.random().toString(36).slice(2)}`;
      this.ws.send(JSON.stringify({ type: "dht_peer_list", requestId }));
      const handler = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "dht_peer_list_response" && msg.requestId === requestId) {
          this.ws.removeEventListener("message", handler);
          resolve(msg.peers || []);
        }
      };
      this.ws.addEventListener("message", handler);
      setTimeout(() => {
        this.ws.removeEventListener("message", handler);
        resolve([]);
      }, 5e3);
    });
  }
  // Fallback: find blob peers via coordinator
  async findBlobPeersFromCoordinator(blobHash) {
    return new Promise((resolve) => {
      const requestId = `dht_blob_${Math.random().toString(36).slice(2)}`;
      this.ws.send(
        JSON.stringify({ type: "dht_find_blob", blobHash, requestId })
      );
      const handler = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "dht_find_blob_response" && msg.requestId === requestId) {
          this.ws.removeEventListener("message", handler);
          resolve(msg.peers || []);
        }
      };
      this.ws.addEventListener("message", handler);
      setTimeout(() => {
        this.ws.removeEventListener("message", handler);
        resolve([]);
      }, 5e3);
    });
  }
  get nodeCount() {
    return this.routingTable.length;
  }
};

// src/gossip.ts
var MESH_SIZE = 6;
var MAX_TTL = 6;
var GOSSIP_INTERVAL = 5e3;
var MAX_SEEN = 1e3;
var GossipSub = class {
  workerId;
  dht;
  sendFn;
  handlers = /* @__PURE__ */ new Map();
  seen = /* @__PURE__ */ new Set();
  ticker = null;
  mesh = [];
  constructor(workerId, dht, sendFn) {
    this.workerId = workerId;
    this.dht = dht;
    this.sendFn = sendFn;
  }
  async start() {
    await this.refreshMesh();
    this.ticker = setInterval(() => this.heartbeat(), GOSSIP_INTERVAL);
    console.log("[scrapower:gossip] started, mesh:", this.mesh.length, "peers");
  }
  stop() {
    if (this.ticker) clearInterval(this.ticker);
    this.mesh = [];
  }
  // Subscribe to a message type
  on(msgType, handler) {
    this.handlers.set(msgType, handler);
  }
  // Broadcast a message to the mesh
  async broadcast(type, data) {
    const msg = {
      type,
      from: this.workerId,
      ttl: MAX_TTL,
      msgId: `${this.workerId}:${Date.now()}:${Math.random().toString(36).slice(2, 6)}`,
      data,
      timestamp: Date.now()
    };
    this.seen.add(msg.msgId);
    await this.sendToMesh(msg);
  }
  // Handle incoming gossip message
  handleMessage(msg) {
    if (this.seen.has(msg.msgId)) return;
    this.seen.add(msg.msgId);
    this.pruneSeen();
    const handler = this.handlers.get(msg.type);
    if (handler) handler(msg);
    if (msg.ttl > 1) {
      msg.ttl--;
      this.sendToMesh(msg).catch(() => {
      });
    }
  }
  // Refresh mesh from DHT routing table
  async refreshMesh() {
    const nodes = await this.dht.findNode(this.workerId);
    this.mesh = nodes.slice(0, MESH_SIZE).map((n) => n.workerId);
  }
  get meshSize() {
    return this.mesh.length;
  }
  async sendToMesh(msg) {
    for (const peerId of this.mesh) {
      if (peerId === this.workerId) continue;
      try {
        await this.sendFn(peerId, msg);
      } catch {
      }
    }
  }
  async heartbeat() {
    await this.broadcast("gossip_heartbeat", {
      workerId: this.workerId,
      meshSize: this.mesh.length
    });
  }
  pruneSeen() {
    if (this.seen.size > MAX_SEEN) {
      const toDelete = this.seen.size - MAX_SEEN / 2;
      let count = 0;
      for (const id of this.seen) {
        if (count >= toDelete) break;
        this.seen.delete(id);
        count++;
      }
    }
  }
};

// src/index.ts
var sandboxCode = await fetch(
  new URL("./sandbox_worker.js", import.meta.url)
).then((r) => r.text());
var sandboxBlob = new Blob([sandboxCode], { type: "application/javascript" });
var sandboxWorker = new Worker(URL.createObjectURL(sandboxBlob));
var BrowserWorker = class {
  ws = null;
  sessionId = "";
  ui = createUI();
  active = true;
  stats = { tasks: 0, cpuMs: 0, dataBytes: 0 };
  hbInterval = 1e4;
  hbTimer = null;
  coordinatorUrl;
  pyodide = null;
  reconnectAttempts = 0;
  reconnectTimer = null;
  p2p = null;
  dht = null;
  gossip = null;
  workerId = "";
  constructor(wsUrl2) {
    this.coordinatorUrl = wsUrl2;
    this.ui.onToggle((active) => {
      this.active = active;
    });
  }
  // ── Lifecycle ──────────────────────────────────────────
  async start() {
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
          reason: "user_disconnect"
        })
      );
      this.ws.close();
    }
  }
  // ── Connection ─────────────────────────────────────────
  async connect() {
    this.ws = new WebSocket(this.coordinatorUrl);
    await new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("WebSocket connection failed"));
    });
    const workerId = `browser-${Math.random().toString(36).slice(2, 10)}`;
    this.workerId = workerId;
    console.log("[scrapower] connecting to", this.coordinatorUrl);
    this.ws.send(
      JSON.stringify({
        type: "hello",
        version: "2.1",
        mode: "persistent",
        worker_id: workerId,
        auth: { method: "none" }
      })
    );
    const sessionMsg = await this.receiveOnce();
    this.sessionId = sessionMsg.session_id;
    this.hbInterval = sessionMsg.heartbeat_interval_ms || 1e4;
    console.log("[scrapower] connected, session:", this.sessionId);
    this.ws.send(
      JSON.stringify({
        type: "capabilities",
        session_id: this.sessionId,
        payload: {
          runtimes: ["wasm", "python"],
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
      this.p2p?.disconnect();
      this.p2p = null;
      this.reconnectAttempts = 0;
      if (this.active) {
        this.ui.update({ connected: false });
        this.scheduleReconnect();
      }
    };
  }
  receiveOnce() {
    return new Promise((resolve) => {
      const handler = (e) => {
        this.ws.removeEventListener("message", handler);
        resolve(JSON.parse(e.data));
      };
      this.ws.addEventListener("message", handler);
    });
  }
  scheduleReconnect() {
    if (!this.active || this.reconnectTimer) return;
    const delay = Math.min(2e3 * Math.pow(2, this.reconnectAttempts), 6e4);
    console.log(
      `[scrapower] reconnecting in ${delay / 1e3}s (attempt ${this.reconnectAttempts + 1})`
    );
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      this.reconnectAttempts++;
      try {
        await this.connect();
        this.reconnectAttempts = 0;
        this.ui.update({ connected: true });
        console.log("[scrapower] reconnected");
      } catch (err) {
        console.error("[scrapower] reconnect failed:", err.message || err);
        this.scheduleReconnect();
      }
    }, delay);
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
  // ── Message handling ───────────────────────────────────
  async handleMessage(msg) {
    if (!this.active) return;
    if (msg.type?.startsWith("p2p_")) {
      if (!this.p2p) {
        this.p2p = new P2PTransport(this.ws, this.workerId, (p2pMsg) => {
          if (p2pMsg.type === "p2p_blob_request") {
            this.handleP2PBlobRequest(p2pMsg);
          }
        });
        this.dht = new DHT(this.ws, this.workerId, this.p2p);
        this.dht.init().catch((e) => console.warn("[scrapower:dht] init failed:", e));
        this.gossip = new GossipSub(
          this.workerId,
          this.dht,
          async (peerId, gMsg) => {
            if (this.p2p) {
              const channel = await this.p2p.connectTo(peerId);
              if (channel) channel.send(JSON.stringify(gMsg));
            }
          }
        );
        this.gossip.on("blob_available", (gMsg) => {
          console.log(
            "[scrapower:gossip] blob",
            gMsg.data.blobHash?.slice(0, 8),
            "from",
            gMsg.from
          );
        });
        this.gossip.start().catch((e) => console.warn("[scrapower:gossip] start failed:", e));
      }
      if (msg.type === "p2p_blob_response" && msg.data?.gossipMsg) {
        this.gossip?.handleMessage(msg.data.gossipMsg);
        return;
      }
      this.p2p.handleMessage(msg);
      return;
    }
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
      await this.executeTask(msg.task);
    }
  }
  // ── Task execution ─────────────────────────────────────
  async executeTask(task) {
    const httpUrl = this.httpUrl();
    try {
      const input = await this.downloadBlob(httpUrl, task.payload.input_hash);
      if (task.runtime === "python") {
        return await this.executePythonTask(task, input);
      }
      const gpuRequired = task.gpu_required || task.resources_required?.gpu_required;
      if (gpuRequired && hasWebGPU()) {
        return await this.executeGpuTask(task, input);
      }
      return await this.executeCpuTask(task, input, httpUrl);
    } catch (err) {
      console.error("[scrapower] task execution failed:", err.message || err);
    }
  }
  async executePythonTask(task, input) {
    console.log("[scrapower] \u{1F40D} Python task:", task.id);
    const start = performance.now();
    if (!this.pyodide) {
      console.log("[scrapower] loading Pyodide...");
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
      await new Promise((resolve, reject) => {
        script.onload = () => resolve();
        script.onerror = () => reject(new Error("Failed to load Pyodide"));
        document.head.appendChild(script);
      });
      this.pyodide = await window.loadPyodide();
      console.log("[scrapower] Pyodide ready");
    }
    try {
      const code = new TextDecoder().decode(input);
      let output = "";
      this.pyodide.setStdout({
        batched: (text) => {
          output += text + "\n";
        }
      });
      await this.pyodide.runPythonAsync(code);
      const durationMs = Math.round(performance.now() - start);
      const outputBytes = new TextEncoder().encode(output || "OK");
      await this.submitResult(task, outputBytes, { outputBytes, durationMs });
    } catch (err) {
      const durationMs = Math.round(performance.now() - start);
      const outputBytes = new TextEncoder().encode(err.message || String(err));
      await this.submitResult(task, outputBytes, {
        outputBytes,
        durationMs,
        error: err.message
      });
    }
  }
  async executeGpuTask(task, input) {
    console.log("[scrapower] \u26A1 GPU task:", task.id);
    const result = await executeGPU(new Uint8Array(input));
    await this.submitResult(task, result.outputBytes, result);
  }
  async executeCpuTask(task, input, httpUrl) {
    console.log("[scrapower] CPU task:", task.id);
    const wasm = await this.downloadBlob(httpUrl, task.payload.executable_hash);
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
  }
  // ── Helpers ────────────────────────────────────────────
  httpUrl() {
    return this.coordinatorUrl.replace("wss://", "https://").replace("ws://", "http://").replace("/worker/ws", "");
  }
  async downloadBlob(httpUrl, hash) {
    console.log("[scrapower] downloading blob:", hash.slice(0, 8));
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
    const resp = await fetch(`${httpUrl}/blobs/${hash}`);
    const data = await resp.arrayBuffer();
    this.gossip?.broadcast("blob_available", { blobHash: hash }).catch(() => {
    });
    return data;
  }
  async handleP2PBlobRequest(msg) {
    const { blobHash, requestId, channel } = msg.data || {};
    console.log("[scrapower] P2P blob request for", blobHash?.slice(0, 8));
    try {
      const httpUrl = this.httpUrl();
      const resp = await fetch(`${httpUrl}/blobs/${blobHash}`);
      const data = await resp.arrayBuffer();
      this.p2p.sendBlobResponse(channel, requestId, data);
    } catch (err) {
      console.error("[scrapower] P2P blob relay failed:", err);
    }
  }
  async submitResult(task, outputBytes, result) {
    const httpUrl = this.httpUrl();
    console.log("[scrapower] uploading result");
    const putResp = await fetch(`${httpUrl}/blobs?assignment_token=${task.assignment_token || ""}`, {
      method: "PUT",
      body: outputBytes
    });
    const { hash: outputHash } = await putResp.json();
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
};
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((err) => console.warn("[scrapower] SW registration failed:", err));
}
var wsUrl = window.SCRAPOWER_WS_URL || (location.protocol === "https:" ? `wss://${location.host}/worker/ws` : `ws://${location.host}/worker/ws`);
var instance = new BrowserWorker(wsUrl);
instance.start();
