# Free Cloud Computing Tiers for Distributed Computing Workers (2026)

> **Research Date:** June 2026
> **Purpose:** Identify all "always free" or perpetually-free cloud compute tiers usable as distributed computing worker nodes, including outbound networking, automation feasibility, timeout limits, and restrictive ToS.

---

## ⭐ TIER 1: Best Candidates for Distributed Workers

These offer real, always-free VM/container compute with full outbound networking and good automation support.

---

### 🔷 Oracle Cloud Always Free

| Aspect | Detail |
|--------|--------|
| **Compute (AMD)** | 2× VM.Standard.E2.1.Micro (1/8 OCPU + 1 GB RAM each) |
| **Compute (ARM)** | 2 OCPUs + 12 GB RAM total (VM.Standard.A1.Flex — can be 1× 2-core/12GB or 2× 1-core/6GB VMs) |
| **Block Storage** | 200 GB total (boot + block volumes combined) |
| **Object Storage** | 20 GB total (Standard + Infrequent Access + Archive) |
| **Outbound Bandwidth** | **10 TB/month** (50 Mbps on x64, 500 Mbps × core count on ARM) |
| **GPU** | None free |
| **Outbound Networking** | Full TCP/UDP, HTTP, WebSocket allowed. Port 25 blocked by default (can request exemption) |
| **Max Runtime** | Always-on (no timeout), BUT **idle instances reclaimed**: if CPU <20%, network <20% for 7 days, instances may be reclaimed |
| **Automation** | Full REST API, Terraform/Resource Manager, CLI, SDKs |
| **Key Restrictions** | Must keep instances active. Idle reclamation policy means you need a keep-alive workload |
| **Always Free?** | ✅ Truly always free (life of account). No credit card needed initially, but upgrade to PAYG recommended to avoid idle reclamation |

**Rating: ⭐⭐⭐⭐⭐** — Best option for distributed workers. ARM instances offer 12 GB RAM, 10 TB egress.

---

### 🔷 Google Cloud Free Tier

| Aspect | Detail |
|--------|--------|
| **Compute Engine** | 1× e2-micro VM (2 vCPU shared, 1 GB RAM), 30 GB HDD, non-preemptible. Restricted to us-west1, us-central1, us-east1 regions |
| **Cloud Run** | 2M requests/month, 360,000 GB-seconds memory, 180,000 vCPU-seconds compute |
| **Cloud Functions** | 2M invocations/month (includes HTTP + background) |
| **App Engine** | 28 frontend instance hours/day, 9 backend instance hours/day |
| **Outbound Bandwidth** | 1 GB/month egress from North America to all regions (excl. China, Australia) |
| **GPU** | None free |
| **Outbound Networking** | Full HTTP/WebSocket/TCP via Compute Engine. Cloud Run and Functions support HTTP (WebSocket via Cloud Run) |
| **Max Runtime** | Compute Engine: Always-on. Cloud Run: 60 min timeout per request. Cloud Functions: 9 min (HTTP), 9 min (event) |
| **Automation** | Full gcloud CLI, REST API, Terraform, Deployment Manager |
| **Key Restrictions** | e2-micro is very constrained (burstable, 1 GB RAM). 1 GB egress is tight |
| **Always Free?** | ✅ Truly always free (not time-limited, no trial expiry) |

**Rating: ⭐⭐⭐** — Good but compute is weak. Best used for lightweight coordination/light tasks.

---

### 🔷 AWS Free Tier

| Aspect | Detail |
|--------|--------|
| **EC2** | **750 hours/month** of t2.micro or t3.micro (1 vCPU, 1 GB RAM) — **12 months only** |
| **Lambda** | 1M requests/month, 400,000 GB-seconds compute |
| **EBS** | 30 GB general purpose SSD or Magnetic |
| **Outbound Bandwidth** | 100 GB/month aggregate egress |
| **GPU** | None free |
| **Outbound Networking** | Full outbound — HTTP/HTTPS/WebSocket/TCP all allowed |
| **Max Runtime** | EC2: Always-on. Lambda: 15 min max per invocation |
| **Automation** | Full AWS CLI, CloudFormation, Terraform, CDK |
| **Key Restrictions** | ⚠️ **EC2 free tier is 12-month trial only.** After 12 months, EC2 is charged. Lambda remains always free (1M requests/month) |
| **Always Free?** | ❌ **EC2 is 12-month trial.** Lambda, S3 (5 GB), DynamoDB (25 GB), SNS, SQS are always free |

**Rating: ⭐⭐** — EC2 is trial-only. Lambda is always free but limited to 15-min execution.

---

### 🔷 Microsoft Azure Free Tier

| Aspect | Detail |
|--------|--------|
| **VMs** | 1× B1S Linux, 1× B1S Windows (1 vCPU, 1 GB RAM) — **12 months only** |
| **App Service** | 10 web/mobile/API apps (60 CPU minutes/day) |
| **Functions** | 1M requests/month |
| **Cosmos DB** | 25 GB, 1000 RUs provisioned |
| **Outbound Bandwidth** | 15 GB inbound (12mo) + 5 GB/month egress |
| **GPU** | None free |
| **Outbound Networking** | Full HTTP/WebSocket/TCP |
| **Max Runtime** | VM: Always-on. Functions: 5 min default (10 min max HTTP). App Service: 240 min timeout |
| **Automation** | Full Azure CLI, ARM Templates, Terraform, Bicep |
| **Key Restrictions** | ⚠️ **VM free tier is 12-month trial only.** Functions and App Service are always free |
| **Always Free?** | ❌ VM is 12-month trial. Always free: Functions (1M/month), App Service (60 CPU-min/day), DevOps |

**Rating: ⭐⭐** — Same pattern as AWS: VMs are trial, serverless is always free.

---

### 🔷 IBM Cloud Free Tier

| Aspect | Detail |
|--------|--------|
| **Compute** | No free VM tier. Cloudant DB: 1 GB. Db2: 100 MB. API Connect: 50k calls. Log Analysis: 500 MB/day |
| **Outbound Networking** | Minimal via API Connect |
| **GPU** | None free |
| **Automation** | CLI, Terraform |
| **Key Restrictions** | No meaningful free compute — limited to databases and API management |
| **Always Free?** | ✅ Always free, but offers no compute instances |

**Rating: ⭐** — Not suitable as a worker node. No free VM/container.

---

## 🔶 TIER 2: Serverless/Edge Compute

These are serverless platforms with free tiers. Suitable for short, stateless tasks but limited for distributed computing.

---

### 🔷 Cloudflare Workers

| Aspect | Detail |
|--------|--------|
| **Requests** | 100,000/day (free plan) |
| **CPU Time** | 10 ms/request |
| **Memory** | 128 MB |
| **Subrequests** | 50/request |
| **Simultaneous Connections** | 6 max per request |
| **Worker Size** | 3 MB (compressed) |
| **Outbound Networking** | ✅ HTTP/HTTPS fetch(), WebSocket (client + server). Full TCP via `connect()` API. 6 concurrent outbound connections per request |
| **Max Runtime** | No duration limit while client connected. Cron: 15 min max |
| **GPU** | None |
| **Automation** | Wrangler CLI, REST API, Terraform, GitHub Actions |
| **Key Restrictions** | 10 ms CPU time is extremely tight for computation. 128 MB RAM. Not suitable for CPU-intensive tasks |
| **Always Free?** | ✅ Always free tier (no trial) |

**Rating: ⭐⭐** — Good for I/O-bound coordination but 10 ms CPU limit kills compute-heavy work.

---

### 🔷 AWS Lambda

| Aspect | Detail |
|--------|--------|
| **Requests** | 1M/month always free |
| **Memory** | Up to 10,240 MB configurable (but GB-seconds limited) |
| **CPU** | Proportional to memory allocation |
| **Outbound Networking** | ✅ Full HTTP/HTTPS, WebSocket, TCP via VPC NAT |
| **Max Runtime** | 15 min per invocation |
| **GPU** | None |
| **Automation** | Full AWS CLI, SDK, Terraform, CDK |
| **Key Restrictions** | 1M requests/month. 400K GB-seconds/month. Pay beyond free tier |
| **Always Free?** | ✅ Always free (not time-limited) |

**Rating: ⭐⭐⭐** — Decent for burstable, short-lived worker tasks up to 15 min.

---

### 🔷 Google Cloud Functions

| Aspect | Detail |
|--------|--------|
| **Requests** | 2M/month |
| **Memory** | Up to 8 GB configurable (but limited by compute-seconds) |
| **Outbound Networking** | ✅ HTTP/HTTPS, WebSocket (experimental) |
| **Max Runtime** | 9 min (HTTP), 9 min (event) |
| **GPU** | None |
| **Automation** | gcloud CLI, Terraform |
| **Key Restrictions** | 2M invocations, 400K GB-seconds, 200K GHz-seconds |
| **Always Free?** | ✅ Always free |

**Rating: ⭐⭐⭐** — Same tier as Lambda. 2M invocations generous.

---

### 🔷 Google Cloud Run

| Aspect | Detail |
|--------|--------|
| **Requests** | 2M/month |
| **Compute** | 360,000 GB-seconds memory, 180,000 vCPU-seconds |
| **Memory** | Up to 32 GB (but limited by seconds) |
| **Concurrency** | Up to 1000 concurrent requests per instance |
| **Outbound Networking** | ✅ Full HTTP/HTTPS. Serverless VPC Access for private networking. WebSocket supported for long-lived connections |
| **Max Runtime** | 60 min per request (default), up to 3600 seconds |
| **GPU** | None free |
| **Automation** | gcloud CLI, Terraform, Cloud Build |
| **Key Restrictions** | 1 GB egress/month. Container-based — stateful work not preserved between requests |
| **Always Free?** | ✅ Always free |

**Rating: ⭐⭐⭐** — Good for containerized workers. 60-min timeout better than Lambda.

---

### 🔷 Azure Functions

| Aspect | Detail |
|--------|--------|
| **Requests** | 1M/month |
| **Memory** | Up to 1.5 GB (Consumption plan) |
| **Outbound Networking** | ✅ HTTP/HTTPS, TCP, Service Bus, Event Hubs |
| **Max Runtime** | 5 min default (10 min max, Consumption) |
| **GPU** | None |
| **Automation** | Azure CLI, Core Tools, Terraform |
| **Key Restrictions** | 400K GB-seconds/month |
| **Always Free?** | ✅ Always free |

**Rating: ⭐⭐** — Short runtime limits. 1M requests decent.

---

### 🔷 Deno Deploy

| Aspect | Detail |
|--------|--------|
| **Requests** | 100,000/day |
| **CPU** | Shared (not publicly specified) |
| **Bandwidth** | 100 GiB data transfer/month |
| **Memory** | Not publicly documented |
| **Outbound Networking** | ✅ Fetch API, WebSocket client |
| **Max Runtime** | 30 seconds per request (soft), 60 seconds (hard) |
| **GPU** | None |
| **Automation** | deployctl CLI, GitHub integration |
| **Key Restrictions** | Very short request timeout. Deno/TypeScript/JavaScript only |
| **Always Free?** | ✅ Always free tier |

**Rating: ⭐⭐** — Decent for lightweight edge tasks. 30s timeout limiting.

---

## 🟡 TIER 3: PaaS / Containers (with free tiers)

---

### 🔷 Render

| Aspect | Detail |
|--------|--------|
| **Web Service** | 750 free instance hours/month |
| **RAM** | 512 MB |
| **CPU** | Shared (not specified) |
| **Storage** | Ephemeral only (lost on spin down). Postgres: 1 GB (expires 30 days) |
| **Bandwidth** | 100 GB/month included, then charged or suspended |
| **Outbound Networking** | ✅ Full HTTP/WebSocket/API calls. Ports 25, 465, 587 (SMTP) blocked |
| **Max Runtime** | **Spins down after 15 min idle.** Spins back up on request (~1 min cold start) |
| **GPU** | None |
| **Automation** | Git push deploy, API, Blueprint (Infrastructure as Code) |
| **Key Restrictions** | 15-min idle spin down prevents continuous compute. WebSocket connections included in idle detection. Render may restart at any time. Free Postgres expires after 30 days |
| **Always Free?** | ✅ Always free, but 750 instance hours/month limits to ~1 always-running service |

**Rating: ⭐⭐** — Idle spin-down makes sustained distributed computing impossible. Good for on-demand tasks only.

---

### 🔷 Fly.io (New signups / PAYG)

| Aspect | Detail |
|--------|--------|
| **Compute** | No longer offers free-tier plans to new customers. All new orgs require credit card and are PAYG |
| **Legacy Free** | Only for accounts created before October 7, 2024: 3× shared-cpu-1x 256 MB VMs, 3 GB persistent storage, 100 GB egress |
| **PAYG Minimum** | shared-cpu-1x 256 MB ≈ $2/month. shared-cpu-1x 1 GB ≈ $5.92/month |
| **Outbound Networking** | ✅ Full. Shared IPv4, IPv6 Anycast. WebSocket, HTTP, TCP all supported |
| **GPU** | Deprecated (unavailable after August 2026) |
| **Automation** | flyctl CLI, REST API, Terraform provider |
| **Always Free?** | ❌ No longer free for new users. Free trial was $5 credit |

**Rating: ⭐** — No longer free for new users. Good platform but PAYG only.

---

### 🔷 Heroku

| Aspect | Detail |
|--------|--------|
| **Compute** | No free dynos since November 2022. Previously free tier eliminated |
| **Postgres** | Mini plan: $5/month (no free tier) |
| **Key Restrictions** | No free compute. Student/GitHub Education may have offers |
| **Always Free?** | ❌ No free tier for compute since 2022 |

**Rating: ❌** — No free tier. Not viable.

---

### 🔷 Railway

| Aspect | Detail |
|--------|--------|
| **Compute** | $5 free credit/month for new users (trial). No perpetual free tier |
| **Specs** | Up to 8 GB RAM, 8 vCPU (consumes credits) |
| **Outbound Networking** | ✅ Full networking |
| **Always Free?** | ❌ Trial credit only ($5). Then PAYG |

**Rating: ❌** — Trial only. No always-free.

---

### 🔷 Koyeb

| Aspect | Detail |
|--------|--------|
| **Compute** | Free tier: 1× nano instance (256 MB RAM, shared CPU, 2.5 GB SSD) |
| **Outbound Bandwidth** | 100 GB/month |
| **Outbound Networking** | ✅ HTTP, TCP, WebSocket |
| **Max Runtime** | Always-on |
| **GPU** | None |
| **Automation** | CLI, API, Git push, Terraform |
| **Key Restrictions** | Free tier limited to one nano service + one database. Service may be subject to rate limits |
| **Always Free?** | ✅ Always free tier available |

**Rating: ⭐⭐⭐** — Always free nano instance. Limited but usable for lightweight workers.

---

### 🔷 Northflank

| Aspect | Detail |
|--------|--------|
| **Free Tier** | 2 services, 2 cron jobs, 1 database |
| **Resources** | Shared CPU (specs not publicly detailed) |
| **Outbound Networking** | ✅ Full |
| **Always Free?** | ✅ Always free tier |

**Rating: ⭐⭐** — Good for small workloads; specs not transparent.

---

### 🔷 Cyclic.sh

| Aspect | Detail |
|--------|--------|
| **Status** | ⚠️ Cyclic.sh appears to have **shut down** or pivoted. No active free tier confirmed for 2026 |

**Rating: ❌** — Unavailable.

---

## 🟢 GPU-Specific Free Tiers

---

### 🔷 Google Colab

| Aspect | Detail |
|--------|--------|
| **GPU** | Nvidia Tesla K80/T4 (free tier), sometimes T4/P100 |
| **CPU** | 2 cores (varies) |
| **RAM** | ~12 GB (varies) |
| **Disk** | ~100 GB (ephemeral) |
| **Outbound Networking** | ✅ HTTP/HTTPS from notebook code. SSH tunneling possible. No persistent server |
| **Max Runtime** | ~12 hours per session (auto-disconnect). Notebook idle timeout ~90 min |
| **GPU Hours** | Not explicitly capped but throttled after heavy usage |
| **Automation** | Via Colab API, notebook scripting. Not designed for headless/automated use |
| **Key Restrictions** | Interactive notebook environment. Not designed for unattended distributed computing. Disconnects aggressively. ToS prohibits cryptomining but generally allows research compute |
| **Always Free?** | ✅ Always free (with usage limits) |

**Rating: ⭐⭐** — GPU access but notebook interface, timeout, and idle disconnects make automation very hacky.

---

### 🔷 Kaggle

| Aspect | Detail |
|--------|--------|
| **CPU** | 4 CPU cores |
| **RAM** | 30 GB |
| **GPU** | With phone verification: 1× Nvidia Tesla P100 OR 2× Nvidia Tesla T4, **30 GPU hours/week** |
| **TPU** | With identity verification: 1× TPU v3-8 (96 cores, 330 GB RAM), 20 hours/week |
| **Storage** | ~100 GB (ephemeral), 14-day dataset persistence |
| **Outbound Networking** | ✅ Full HTTP/HTTPS via notebook code. Can download/upload data |
| **Max Runtime** | 9 hours per session. Weekly GPU limit (30 hours) |
| **Automation** | Notebook-based. Limited CLI. API available but designed for Kaggle competition workflows |
| **Key Restrictions** | Weekly GPU limit. 9-hour session cap. ToS permits research/ML. No sustained server |
| **Always Free?** | ✅ Always free |

**Rating: ⭐⭐⭐** — Best free GPU option. 30 GB RAM + P100/T4 GPU, but notebook-only and 9-hour session cap.

---

### 🔷 Paperspace Gradient

| Aspect | Detail |
|--------|--------|
| **Free Tier** | Public projects, 5 GB storage, basic instances |
| **GPU** | Free tier typically CPU-only. M4000 GPU sometimes available on free (limited) |
| **Outbound Networking** | ✅ Yes |
| **Max Runtime** | 6 hours per session on free notebooks |
| **Automation** | CLI, API, GitHub integration |
| **Always Free?** | ✅ Free tier exists but very limited compute |

**Rating: ⭐⭐** — GPU sometimes available but not guaranteed. Short sessions.

---

### 🔷 Hugging Face Spaces

| Aspect | Detail |
|--------|--------|
| **Compute** | 2 vCPU, 16 GB RAM (free CPU spaces) |
| **GPU** | None free. GPU Spaces require paid subscription |
| **Storage** | 50 GB (free) |
| **Outbound Networking** | ✅ HTTP/HTTPS. Can serve web apps/APIs |
| **Max Runtime** | Always-on (spaces run 24/7) |
| **Automation** | Git push, API. Docker-based |
| **Key Restrictions** | No free GPU. CPU only on free tier |
| **Always Free?** | ✅ Free CPU tier |

**Rating: ⭐⭐⭐** — Excellent for CPU inference/small workers. Always-on, 16 GB RAM. No GPU.

---

### 🔷 Lightning.ai

| Aspect | Detail |
|--------|--------|
| **Free Tier** | 22 GPU hours/month (varies by program). Primarily focused on AI training |
| **GPU** | Varies (access to A100, A10G, etc. on free credits) |
| **Outbound Networking** | ✅ Via Studio environment |
| **Max Runtime** | Session-based, varies |
| **Automation** | CLI, API |
| **Key Restrictions** | Free tier program may change. Primarily trial-oriented |
| **Always Free?** | ⚠️ More of a trial/freemium model |

**Rating: ⭐⭐** — Good for ML but not ideal for general distributed computing.

---

### 🔷 JarvisLabs

| Aspect | Detail |
|--------|--------|
| **Status** | ⚠️ Has been superseded/consolidated. No clearly advertised free tier in 2026 |

**Rating: ❌** — Not available.

---

### 🔷 Modal

| Aspect | Detail |
|--------|--------|
| **Free Credits** | $30/month free credits (may be $5 for some accounts) |
| **GPU** | Credits can be used for GPU instances (A100, T4, etc.) |
| **Outbound Networking** | ✅ Full. Python-native API |
| **Max Runtime** | Configurable, up to days for long-running functions |
| **Automation** | Python SDK, CLI |
| **Key Restrictions** | Credit-based model. Credits expire monthly |
| **Always Free?** | ✅ $30/month free credits, not time-limited |

**Rating: ⭐⭐⭐⭐** — Excellent free credit amount. Python-native approach great for distributed compute.

---

### 🔷 Replicate

| Aspect | Detail |
|--------|--------|
| **Free Tier** | Limited free inference runs (model-dependent). Not for custom compute |
| **GPU** | Access to hosted models (LLMs, diffusion). Can't run arbitrary code |
| **Outbound Networking** | HTTP API only |
| **Key Restrictions** | Model inference only. Cannot run arbitrary compute workloads |
| **Always Free?** | ❌ Free tier is extremely limited |

**Rating: ⭐** — Not suitable. Inference-only, not general compute.

---

### 🔷 RunPod Serverless

| Aspect | Detail |
|--------|--------|
| **Free Tier** | No free tier for serverless. Minimum charge per request |
| **Always Free?** | ❌ No free tier |

**Rating: ❌** — No free tier.

---

### 🔷 Banana.dev

| Aspect | Detail |
|--------|--------|
| **Free Tier** | No public free tier in 2026. Previously had developer credits |
| **Always Free?** | ❌ |

**Rating: ❌** — No free tier.

---

### 🔷 Lambda Labs (Lambda GPU Cloud)

| Aspect | Detail |
|--------|--------|
| **Free Tier** | No free tier. Pay per GPU-hour |

**Rating: ❌** — No free tier.

---

## 🟠 BaaS/Database (if they allow compute)

---

### 🔷 Supabase

| Aspect | Detail |
|--------|--------|
| **Database** | PostgreSQL, 500 MB, 2 projects |
| **Auth** | 50,000 MAU |
| **Edge Functions** | 500,000 invocations/month, 2 MB size, 128 MB RAM, up to 400 seconds/execution |
| **Outbound Networking** | ✅ from Edge Functions |
| **GPU** | None |
| **Automation** | CLI, API, GitHub integration |
| **Key Restrictions** | Edge Functions are Deno-based. DB can't run arbitrary compute |
| **Always Free?** | ✅ Always free |

**Rating: ⭐⭐** — Edge Functions useful for lightweight coordination; DB not for compute.

---

### 🔷 Firebase

| Aspect | Detail |
|--------|--------|
| **Functions** | 2M invocations/month (Spark plan) |
| **Outbound Networking** | ✅ (Functions can call external APIs) |
| **Max Runtime** | 9 min per function call |
| **GPU** | None |
| **Key Restrictions** | Functions only. No general compute. Google Cloud integration |
| **Always Free?** | ✅ Always free Spark plan |

**Rating: ⭐⭐** — Functions only. Same as Google Cloud Functions.

---

### 🔷 Appwrite

| Aspect | Detail |
|--------|--------|
| **Functions** | Node.js, Python, PHP, Ruby, Deno, Dart. 5 functions/project, 900 sec (15 min) timeout, 128-3072 MB RAM |
| **Database** | 1 database per project, 3 buckets |
| **Outbound Networking** | ✅ HTTP/HTTPS from functions |
| **Automation** | CLI, API, Git integration |
| **Key Restrictions** | Limited to 5 functions per project. Not for heavy compute |
| **Always Free?** | ✅ Always free (unlimited projects) |

**Rating: ⭐⭐** — Good as lightweight worker. 15-min timeout decent.

---

### 🔷 Neon (PostgreSQL)

| Aspect | Detail |
|--------|--------|
| **Compute** | No compute (database only). Edge Functions integration |
| **Database** | 0.5 GB/project, 100 projects, 20 hours active/month for non-primary branches, primary branch: auto-suspend after 5 min |
| **Key Restrictions** | No general compute platform |

**Rating: ⭐** — Database only. Not a worker node.

---

### 🔷 Turso (SQLite Edge)

| Aspect | Detail |
|--------|--------|
| **Free Tier** | 9 GB total storage, 500 databases, 3 locations, 1 billion row reads/month |
| **Compute** | SQL only. No general compute |
| **Key Restrictions** | Database only |

**Rating: ⭐** — Database only.

---

## 📊 Summary Comparison (Best Candidates)

| Provider | CPU | RAM | Storage | Bandwidth | GPU | Always? | Timeout | Auto? | Rating |
|----------|-----|-----|---------|-----------|-----|---------|---------|-------|--------|
| **Oracle Cloud** | 2 ARM cores + 2 micro AMD | 12 GB + 2 GB | 200 GB | 10 TB/mo | No | ✅ Yes | Always-on (idle risk) | ✅ | ⭐⭐⭐⭐⭐ |
| **Google Compute** | 1× e2-micro | 1 GB | 30 GB | 1 GB/mo | No | ✅ Yes | Always-on | ✅ | ⭐⭐⭐ |
| **Google Cloud Run** | Burstable | Config | Ephemeral | 1 GB/mo | No | ✅ Yes | 60 min/req | ✅ | ⭐⭐⭐ |
| **AWS Lambda** | Prop. to RAM | Config | Ephemeral | 100 GB/mo | No | ✅ Yes | 15 min | ✅ | ⭐⭐⭐ |
| **GCP Functions** | Prop. to RAM | Config | Ephemeral | 1 GB/mo | No | ✅ Yes | 9 min | ✅ | ⭐⭐⭐ |
| **Koyeb** | Shared | 256 MB | 2.5 GB | 100 GB/mo | No | ✅ Yes | Always-on | ✅ | ⭐⭐⭐ |
| **Modal** | Various | Various | Config | Config | ✅ (credits) | ✅ Yes ($30/mo) | Config | ✅ | ⭐⭐⭐⭐ |
| **Kaggle** | 4 cores | 30 GB | ~100 GB | Ephemeral | ✅ P100/T4 | ✅ Yes | 9 hrs/session | ⚠️ Partial | ⭐⭐⭐ |
| **HF Spaces** | 2 vCPU | 16 GB | 50 GB | Unlimited | No | ✅ Yes | Always-on | ✅ | ⭐⭐⭐ |
| **Cloudflare Workers** | Burstable | 128 MB | Ephemeral | 100k req/day | No | ✅ Yes | No limit | ✅ | ⭐⭐ |
| **Deno Deploy** | Shared | TBD | Ephemeral | 100 GiB/mo | No | ✅ Yes | 30s | ✅ | ⭐⭐ |
| **Render** | Shared | 512 MB | Ephemeral | 100 GB/mo | No | ✅ Yes | 15-min idle | ✅ | ⭐⭐ |
| **Colab** | 2 cores | ~12 GB | ~100 GB | ✅ | ✅ T4/K80 | ✅ Yes | ~12 hrs | ❌ Hard | ⭐⭐ |

---

## 🎯 Recommended Strategy for Distributed Computing

### Primary Workers (Always-on)
1. **Oracle Cloud Always Free** — Deploy 2× ARM VMs (1 OCPU + 6 GB RAM each) or 1× 2 OCPU + 12 GB RAM. Use the 2× AMD micro instances for lightweight coordination. Keep-alive workload to avoid idle reclamation. 10 TB egress is unmatched.
2. **Koyeb** — 1× nano instance as a lightweight task dispatcher.
3. **Hugging Face Spaces** — CPU-only Docker containers with 16 GB RAM and 50 GB storage for large data processing.

### Burst/Supplemental Workers
4. **Google Cloud Run** — Container-based workers for spike workloads. 2M requests/month free.
5. **AWS Lambda** / **GCP Functions** — Stateless workers for small, parallel tasks up to 15/9 min each.
6. **Cloudflare Workers** — I/O-bound coordination tasks (WebSocket relays, health checks, dispatch).

### GPU Workers
7. **Kaggle** — Best free GPU. P100/T4 with 30 GB RAM. 30 GPU hours/week. Integrate via notebook automation (challenging but possible).
8. **Modal** — $30/month credits for GPU instances. Python-native, excellent for ML training/inference workers.
9. **Google Colab** — Backup GPU resource but unreliable due to aggressive disconnects.

### Orchestration
- Use a lightweight orchestrator on Oracle Cloud ARM instances to dispatch tasks.
- WebSocket connections for worker coordination (all major providers support WS).
- Use Cloudflare Workers as a global dispatch/API layer.
- Consider Modal for GPU-accelerated tasks paid with free credits.

---

## ⚠️ Critical Caveats

1. **AWS/Azure EC2/VM free tier = 12-month trial only.** Do not build long-term infrastructure relying on these.
2. **Oracle Cloud idle reclamation** means you must keep instances active (>20% CPU in 7-day rolling window) or risk termination.
3. **Render idle spin-down** (15 min) makes it fundamentally unsuitable for any continuous worker.
4. **Fly.io** no longer offers free tiers to new accounts.
5. **Kaggle / Colab** ToS prohibit cryptomining and may prohibit other automated non-interactive use. Review ToS carefully.
6. **Serverless cold starts** add latency. Not ideal for latency-sensitive distributed computing.
7. **Bandwidth limits** on Google Cloud (1 GB) and Azure (5 GB) are the most restrictive. Oracle's 10 TB is the best for data-intensive work.
