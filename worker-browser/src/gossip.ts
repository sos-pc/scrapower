// GossipSub — P2P pub/sub for worker-to-worker communication.
// Workers form a mesh and broadcast messages via WebRTC Data Channels.
//
// Messages propagate in O(log N) hops through the P2P network.
// Used for: blob announcements, task availability, peer heartbeats.

import { DHT } from "./dht";

interface GossipMessage {
  type: string;
  from: string;
  ttl: number;       // hops remaining
  msgId: string;     // deduplication
  data: any;
  timestamp: number;
}

const MESH_SIZE = 6;       // target number of mesh peers
const MAX_TTL = 6;         // max hops (~log₂(50000) ≈ 16, reduced for our scale)
const GOSSIP_INTERVAL = 5000; // heartbeat interval (ms)
const MAX_SEEN = 1000;     // max seen message cache

export class GossipSub {
  private workerId: string;
  private dht: DHT;
  private sendFn: (peerId: string, msg: GossipMessage) => Promise<void>;
  private handlers: Map<string, (msg: GossipMessage) => void> = new Map();
  private seen: Set<string> = new Set();
  private ticker: ReturnType<typeof setInterval> | null = null;
  private mesh: string[] = [];

  constructor(
    workerId: string,
    dht: DHT,
    sendFn: (peerId: string, msg: GossipMessage) => Promise<void>,
  ) {
    this.workerId = workerId;
    this.dht = dht;
    this.sendFn = sendFn;
  }

  async start(): Promise<void> {
    // Build initial mesh from DHT routing table
    await this.refreshMesh();
    this.ticker = setInterval(() => this.heartbeat(), GOSSIP_INTERVAL);
    console.log("[scrapower:gossip] started, mesh:", this.mesh.length, "peers");
  }

  stop(): void {
    if (this.ticker) clearInterval(this.ticker);
    this.mesh = [];
  }

  // Subscribe to a message type
  on(msgType: string, handler: (msg: GossipMessage) => void): void {
    this.handlers.set(msgType, handler);
  }

  // Broadcast a message to the mesh
  async broadcast(type: string, data: any): Promise<void> {
    const msg: GossipMessage = {
      type,
      from: this.workerId,
      ttl: MAX_TTL,
      msgId: `${this.workerId}:${Date.now()}:${Math.random().toString(36).slice(2, 6)}`,
      data,
      timestamp: Date.now(),
    };
    this.seen.add(msg.msgId);
    await this.sendToMesh(msg);
  }

  // Handle incoming gossip message
  handleMessage(msg: GossipMessage): void {
    // Deduplicate
    if (this.seen.has(msg.msgId)) return;
    this.seen.add(msg.msgId);
    this.pruneSeen();

    // Deliver to local handlers
    const handler = this.handlers.get(msg.type);
    if (handler) handler(msg);

    // Re-broadcast if TTL > 0
    if (msg.ttl > 1) {
      msg.ttl--;
      this.sendToMesh(msg).catch(() => {});
    }
  }

  // Refresh mesh from DHT routing table
  async refreshMesh(): Promise<void> {
    const nodes = await this.dht.findNode(this.workerId);
    this.mesh = nodes.slice(0, MESH_SIZE).map((n) => n.workerId);
  }

  get meshSize(): number {
    return this.mesh.length;
  }

  private async sendToMesh(msg: GossipMessage): Promise<void> {
    for (const peerId of this.mesh) {
      if (peerId === this.workerId) continue;
      try {
        await this.sendFn(peerId, msg);
      } catch {
        // Peer unreachable, will be pruned later
      }
    }
  }

  private async heartbeat(): Promise<void> {
    await this.broadcast("gossip_heartbeat", {
      workerId: this.workerId,
      meshSize: this.mesh.length,
    });
  }

  private pruneSeen(): void {
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
}
