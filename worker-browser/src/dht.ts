// Kademlia-style DHT for worker discovery and routing.
// Runs on top of WebRTC (P2P) + WebSocket (bootstrap/coordination).
//
// Key operations:
//   findNode(target) → K closest peers to a target ID
//   store(key, value) → store on K closest peers
//   findValue(key) → retrieve from K closest peers

import { P2PTransport } from "./p2p";

interface DHTNode {
  id: string; // hex-encoded node ID (SHA-256 of worker_id)
  workerId: string;
}

interface DHTStoreEntry {
  key: string;
  value: string;
  timestamp: number;
}

const K = 8; // replication factor
const ALPHA = 3; // concurrency for lookups

async function sha256Hex(input: string): Promise<string> {
  const hashBuffer = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(input),
  );
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function xorDistance(a: string, b: string): bigint {
  return BigInt("0x" + a) ^ BigInt("0x" + b);
}

export class DHT {
  private nodeId: string;
  private workerId: string;
  private p2p: P2PTransport;
  private routingTable: DHTNode[] = []; // sorted by XOR distance from own node ID
  private _store: Map<string, DHTStoreEntry> = new Map();
  private ws: WebSocket;

  constructor(ws: WebSocket, workerId: string, p2p: P2PTransport) {
    this.ws = ws;
    this.workerId = workerId;
    this.p2p = p2p;
    this.nodeId = ""; // set async via init()
  }

  async init(): Promise<void> {
    this.nodeId = await sha256Hex(this.workerId);
    await this.bootstrap();
    console.log(
      "[scrapower:dht] initialized, nodeId:",
      this.nodeId.slice(0, 12),
    );
  }

  // Bootstrap: ask coordinator for list of active workers, then ping them
  async bootstrap(): Promise<void> {
    // Request peer list from coordinator
    const peers = await this.requestPeerList();
    console.log("[scrapower:dht] bootstrap: found", peers.length, "peers");

    // Ping each peer to fill routing table
    for (const peerId of peers.slice(0, K * 2)) {
      if (peerId === this.workerId) continue;
      await this.ping(peerId);
    }
  }

  // Find the K closest nodes to a target (Kademlia FIND_NODE)
  async findNode(targetId: string): Promise<DHTNode[]> {
    // Sort our routing table by XOR distance to target
    const candidates = [...this.routingTable].sort((a, b) =>
      Number(xorDistance(a.id, targetId) - xorDistance(b.id, targetId)),
    );
    return candidates.slice(0, K);
  }

  // Store a key-value pair on the K closest nodes
  async store(key: string, value: string): Promise<void> {
    const targetId = await sha256Hex(key);
    const closest = await this.findNode(targetId);

    // Store locally
    this._store.set(key, { key, value, timestamp: Date.now() });

    // Replicate to closest peers
    for (const node of closest) {
      try {
        await this.p2p.requestBlob(node.workerId, key); // ping via P2P
      } catch {
        // Node unreachable, will be pruned later
      }
    }
  }

  // Find a value in the DHT
  async findValue(key: string): Promise<string | null> {
    // Check local store first
    const local = this._store.get(key);
    if (local) return local.value;

    // Query K closest nodes
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
  async advertiseBlob(blobHash: string): Promise<void> {
    await this.store(`blob:${blobHash}`, this.workerId);
  }

  // Find which workers have a blob
  async findBlobWorkers(blobHash: string): Promise<string[]> {
    const value = await this.findValue(`blob:${blobHash}`);
    if (value) return [value];

    // Fallback: ask coordinator (for blobs stored before this DHT session)
    return this.findBlobPeersFromCoordinator(blobHash);
  }

  // Ping a peer and add to routing table
  private async ping(peerWorkerId: string): Promise<void> {
    const peerNodeId = await sha256Hex(peerWorkerId);
    const existing = this.routingTable.find((n) => n.id === peerNodeId);
    if (existing) return; // already known

    try {
      // Just test connectivity via P2P — don't actually request a blob
      const channel = await this.p2p.connectTo(peerWorkerId);
      if (channel) {
        this.routingTable.push({ id: peerNodeId, workerId: peerWorkerId });
        // Keep routing table sorted by distance from own node
        this.routingTable.sort((a, b) =>
          Number(
            xorDistance(a.id, this.nodeId) - xorDistance(b.id, this.nodeId),
          ),
        );
        // Limit size
        if (this.routingTable.length > K * 20) {
          this.routingTable = this.routingTable.slice(0, K * 20);
        }
      }
    } catch {
      // Peer unreachable, skip
    }
  }

  // Remove stale entries
  prune(): void {
    const now = Date.now();
    this._store.forEach((entry, key) => {
      if (now - entry.timestamp > 3600_000) {
        this._store.delete(key);
      }
    });
  }

  // Get peer list from coordinator
  private async requestPeerList(): Promise<string[]> {
    return new Promise((resolve) => {
      const requestId = `dht_peers_${Math.random().toString(36).slice(2)}`;
      this.ws.send(JSON.stringify({ type: "dht_peer_list", requestId }));

      const handler = (e: MessageEvent) => {
        const msg = JSON.parse(e.data);
        if (
          msg.type === "dht_peer_list_response" &&
          msg.requestId === requestId
        ) {
          this.ws.removeEventListener("message", handler);
          resolve(msg.peers || []);
        }
      };
      this.ws.addEventListener("message", handler);
      setTimeout(() => {
        this.ws.removeEventListener("message", handler);
        resolve([]);
      }, 5000);
    });
  }

  // Fallback: find blob peers via coordinator
  private async findBlobPeersFromCoordinator(
    blobHash: string,
  ): Promise<string[]> {
    return new Promise((resolve) => {
      const requestId = `dht_blob_${Math.random().toString(36).slice(2)}`;
      this.ws.send(
        JSON.stringify({ type: "dht_find_blob", blobHash, requestId }),
      );

      const handler = (e: MessageEvent) => {
        const msg = JSON.parse(e.data);
        if (
          msg.type === "dht_find_blob_response" &&
          msg.requestId === requestId
        ) {
          this.ws.removeEventListener("message", handler);
          resolve(msg.peers || []);
        }
      };
      this.ws.addEventListener("message", handler);
      setTimeout(() => {
        this.ws.removeEventListener("message", handler);
        resolve([]);
      }, 5000);
    });
  }

  get nodeCount(): number {
    return this.routingTable.length;
  }
}
