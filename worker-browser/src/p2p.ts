// P2P transport via WebRTC Data Channels.
// Workers exchange blobs directly, bypassing the coordinator.
// Signalling goes through the existing WebSocket connection.

interface PeerConnection {
  pc: RTCPeerConnection;
  channel: RTCDataChannel | null;
  workerId: string;
}

interface P2PMessage {
  type:
    | "p2p_offer"
    | "p2p_answer"
    | "p2p_ice"
    | "p2p_blob_request"
    | "p2p_blob_response";
  from: string;
  to: string;
  data?: any;
}

const ICE_SERVERS: RTCConfiguration = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

export class P2PTransport {
  private ws: WebSocket;
  private workerId: string;
  private peers: Map<string, PeerConnection> = new Map();
  private blobCallbacks: Map<string, (data: ArrayBuffer) => void> = new Map();
  private onSignal: (msg: P2PMessage) => void;

  constructor(
    ws: WebSocket,
    workerId: string,
    onSignal: (msg: P2PMessage) => void,
  ) {
    this.ws = ws;
    this.workerId = workerId;
    this.onSignal = onSignal;
  }

  async handleMessage(msg: P2PMessage) {
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
  async requestBlob(
    peerWorkerId: string,
    blobHash: string,
  ): Promise<ArrayBuffer> {
    const peer = this.peers.get(peerWorkerId);
    if (peer?.channel?.readyState === "open")
      return this.sendBlobRequest(peer, blobHash);
    return this.connectAndRequest(peerWorkerId, blobHash);
  }

  // Connect to peer (for DHT ping), returns channel or null
  async connectTo(peerWorkerId: string): Promise<RTCDataChannel | null> {
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
  async findBlobPeers(blobHash: string): Promise<string[]> {
    return new Promise((resolve) => {
      const rid = `find_${Math.random().toString(36).slice(2)}`;
      this.ws.send(
        JSON.stringify({
          type: "p2p_blob_request",
          from: this.workerId,
          to: "",
          data: { blobHash, requestId: rid },
        }),
      );
      const h = (e: MessageEvent) => {
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
      }, 5000);
    });
  }

  private async connectAndRequest(
    peerWorkerId: string,
    blobHash: string,
  ): Promise<ArrayBuffer> {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    const channel = pc.createDataChannel("scrapower-blob");
    const peer: PeerConnection = { pc, channel, workerId: peerWorkerId };
    this.peers.set(peerWorkerId, peer);

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("P2P timeout")), 15000);
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
            data: e.candidate,
          });
      };
      pc.createOffer()
        .then((offer) => {
          pc.setLocalDescription(offer);
          this.sendSignal({
            type: "p2p_offer",
            from: this.workerId,
            to: peerWorkerId,
            data: offer,
          });
        })
        .catch(reject);
    });
  }

  private async sendBlobRequest(
    peer: PeerConnection,
    blobHash: string,
  ): Promise<ArrayBuffer> {
    const rid = Math.random().toString(36).slice(2, 10);
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("Blob timeout")), 30000);
      this.blobCallbacks.set(rid, (d) => {
        clearTimeout(t);
        resolve(d);
      });
      peer.channel!.send(
        JSON.stringify({ type: "blob_request", blobHash, requestId: rid }),
      );
    });
  }

  sendBlobResponse(
    channel: RTCDataChannel,
    requestId: string,
    data: ArrayBuffer,
  ) {
    const bytes = new Uint8Array(data);
    const CHUNK = 16384;
    const total = Math.ceil(bytes.length / CHUNK);
    channel.send(
      JSON.stringify({
        type: "blob_response_start",
        requestId,
        totalChunks: total,
        totalSize: bytes.length,
      }),
    );
    for (let i = 0; i < total; i++)
      channel.send(bytes.slice(i * CHUNK, (i + 1) * CHUNK));
  }

  private async handleOffer(msg: P2PMessage) {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    const peer: PeerConnection = { pc, channel: null, workerId: msg.from };
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
          data: e.candidate,
        });
    };
    await pc.setRemoteDescription(new RTCSessionDescription(msg.data));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    this.sendSignal({
      type: "p2p_answer",
      from: this.workerId,
      to: msg.from,
      data: answer,
    });
  }

  private async handleAnswer(msg: P2PMessage) {
    const peer = this.peers.get(msg.from);
    if (peer)
      await peer.pc.setRemoteDescription(new RTCSessionDescription(msg.data));
  }

  private async handleIce(msg: P2PMessage) {
    const peer = this.peers.get(msg.from);
    if (peer?.data)
      await peer.pc.addIceCandidate(new RTCIceCandidate(msg.data));
  }

  private handleBlobResponse(msg: P2PMessage) {
    if (msg.data?.blobData) {
      const cb = this.blobCallbacks.get(msg.data.requestId);
      if (cb) {
        cb(new Uint8Array(msg.data.blobData).buffer);
        this.blobCallbacks.delete(msg.data.requestId);
      }
    }
  }

  private setupIncoming(channel: RTCDataChannel) {
    const chunks: Map<
      string,
      { total: number; data: Uint8Array[]; size: number }
    > = new Map();
    channel.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data);
        if (m.type === "blob_request")
          this.onSignal({
            type: "p2p_blob_request",
            from: "",
            to: this.workerId,
            data: m,
          });
        else if (m.type === "blob_response_start")
          chunks.set(m.requestId, {
            total: m.totalChunks,
            data: [],
            size: m.totalSize,
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

  private sendSignal(msg: P2PMessage) {
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

  get peerCount(): number {
    return this.peers.size;
  }
}
