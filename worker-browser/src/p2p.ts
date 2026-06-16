// P2P transport via WebRTC Data Channels.
// Workers exchange blobs directly, bypassing the coordinator.
// Signalling goes through the existing WebSocket connection.

interface PeerConnection {
  pc: RTCPeerConnection;
  channel: RTCDataChannel | null;
  workerId: string;
}

interface P2PMessage {
  type: "p2p_offer" | "p2p_answer" | "p2p_ice" | "p2p_blob_request" | "p2p_blob_response";
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

  constructor(ws: WebSocket, workerId: string, onSignal: (msg: P2PMessage) => void) {
    this.ws = ws;
    this.workerId = workerId;
    this.onSignal = onSignal;
  }

  // Called when a P2P message arrives from the coordinator
  async handleMessage(msg: P2PMessage) {
    if (msg.to !== this.workerId) return; // not for us

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
  async requestBlob(peerWorkerId: string, blobHash: string): Promise<ArrayBuffer> {
    const peer = this.peers.get(peerWorkerId);
    if (peer?.channel && peer.channel.readyState === "open") {
      return this.sendBlobRequest(peer, blobHash);
    }

    // No existing connection → create one
    return this.connectAndRequest(peerWorkerId, blobHash);
  }

  // Check which peers have a given blob (via coordinator)
  async findBlobPeers(blobHash: string): Promise<string[]> {
    return new Promise((resolve) => {
      const requestId = `find_${Math.random().toString(36).slice(2)}`;
      this.sendSignal({ type: "p2p_blob_request", from: this.workerId, to: "", data: { blobHash, requestId } });

      const handler = (e: MessageEvent) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "p2p_blob_peers" && msg.requestId === requestId) {
          this.ws.removeEventListener("message", handler);
          resolve(msg.peers || []);
        }
      };
      this.ws.addEventListener("message", handler);

      // Timeout after 5s
      setTimeout(() => {
        this.ws.removeEventListener("message", handler);
        resolve([]);
      }, 5000);
    });
  }

  private async connectAndRequest(peerWorkerId: string, blobHash: string): Promise<ArrayBuffer> {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    const channel = pc.createDataChannel("scrapower-blob");

    const peer: PeerConnection = { pc, channel, workerId: peerWorkerId };
    this.peers.set(peerWorkerId, peer);

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error(`P2P connection to ${peerWorkerId} timed out`));
      }, 15000);

      channel.onopen = async () => {
        clearTimeout(timeout);
        try {
          const data = await this.sendBlobRequest(peer, blobHash);
          resolve(data);
        } catch (err) {
          reject(err);
        }
      };

      pc.onicecandidate = (e) => {
        if (e.candidate) {
          this.sendSignal({
            type: "p2p_ice", from: this.workerId, to: peerWorkerId,
            data: e.candidate,
          });
        }
      };

      pc.createOffer().then((offer) => {
        pc.setLocalDescription(offer);
        this.sendSignal({
          type: "p2p_offer", from: this.workerId, to: peerWorkerId,
          data: offer,
        });
      }).catch(reject);
    });
  }

  private async sendBlobRequest(peer: PeerConnection, blobHash: string): Promise<ArrayBuffer> {
    const requestId = Math.random().toString(36).slice(2, 10);
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Blob request timeout")), 30000);
      this.blobCallbacks.set(requestId, (data) => {
        clearTimeout(timeout);
        resolve(data);
      });

      peer.channel!.send(JSON.stringify({ type: "blob_request", blobHash, requestId }));
    });
  }

  // Handle incoming blob request from a peer
  onBlobRequest(blobHash: string, channel: RTCDataChannel) {
    // The caller should fetch the blob from IndexedDB/cache and send it back
    // This is wired externally
    return { channel, blobHash };
  }

  sendBlobResponse(channel: RTCDataChannel, requestId: string, data: ArrayBuffer) {
    const CHUNK_SIZE = 16384;
    const bytes = new Uint8Array(data);
    const totalChunks = Math.ceil(bytes.length / CHUNK_SIZE);

    channel.send(JSON.stringify({ type: "blob_response_start", requestId, totalChunks, totalSize: bytes.length }));

    for (let i = 0; i < totalChunks; i++) {
      const chunk = bytes.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
      channel.send(chunk);
    }
  }

  private async handleOffer(msg: P2PMessage) {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    const peer: PeerConnection = { pc, channel: null, workerId: msg.from };
    this.peers.set(msg.from, peer);

    pc.ondatachannel = (e) => {
      peer.channel = e.channel;
      this.setupIncomingChannel(e.channel);
    };

    pc.onicecandidate = (e) => {
      if (e.candidate) {
        this.sendSignal({
          type: "p2p_ice", from: this.workerId, to: msg.from,
          data: e.candidate,
        });
      }
    };

    await pc.setRemoteDescription(new RTCSessionDescription(msg.data));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    this.sendSignal({ type: "p2p_answer", from: this.workerId, to: msg.from, data: answer });
  }

  private async handleAnswer(msg: P2PMessage) {
    const peer = this.peers.get(msg.from);
    if (peer) {
      await peer.pc.setRemoteDescription(new RTCSessionDescription(msg.data));
    }
  }

  private async handleIce(msg: P2PMessage) {
    const peer = this.peers.get(msg.from);
    if (peer && msg.data) {
      await peer.pc.addIceCandidate(new RTCIceCandidate(msg.data));
    }
  }

  private handleBlobResponse(msg: P2PMessage) {
    // Chunked blob response — reassemble
    // Simplified: for small blobs, single message
    if (msg.data?.blobData) {
      const callback = this.blobCallbacks.get(msg.data.requestId);
      if (callback) {
        callback(new Uint8Array(msg.data.blobData).buffer);
        this.blobCallbacks.delete(msg.data.requestId);
      }
    }
  }

  private setupIncomingChannel(channel: RTCDataChannel) {
    const receivedChunks: Map<string, { totalChunks: number; chunks: Uint8Array[]; totalSize: number }> = new Map();

    channel.onmessage = (e) => {
      if (typeof e.data === "string") {
        const msg = JSON.parse(e.data);
        if (msg.type === "blob_request") {
          // External handler should call sendBlobResponse
          this.onSignal({ type: "p2p_blob_request", from: "", to: this.workerId, data: msg });
        } else if (msg.type === "blob_response_start") {
          receivedChunks.set(msg.requestId, { totalChunks: msg.totalChunks, chunks: [], totalSize: msg.totalSize });
        }
      } else if (e.data instanceof ArrayBuffer || e.data instanceof Uint8Array) {
        // Binary chunk — find which request this belongs to
        // Simplified: for now, assume the most recent request
        for (const [reqId, state] of receivedChunks) {
          if (state.chunks.length < state.totalChunks) {
            state.chunks.push(new Uint8Array(e.data));
            if (state.chunks.length === state.totalChunks) {
              // Reassemble
              const result = new Uint8Array(state.totalSize);
              let offset = 0;
              for (const chunk of state.chunks) {
                result.set(chunk, offset);
                offset += chunk.length;
              }
              receivedChunks.delete(reqId);
              const callback = this.blobCallbacks.get(reqId);
              if (callback) {
                callback(result.buffer);
                this.blobCallbacks.delete(reqId);
              }
            }
            break;
          }
        }
      }
    };
  }

  private sendSignal(msg: P2PMessage) {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  disconnect() {
    for (const peer of this.peers.values()) {
      peer.channel?.close();
      peer.pc.close();
    }
    this.peers.clear();
  }
}
