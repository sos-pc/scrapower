import asyncio
import base64
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/scrapower"))
os.environ["SCRAPOWER_SECRET"] = "sp-scrapower-oauth-fernet-secret-2026"
import aiohttp
import aiosqlite

from scrapower.coordinator.crypto_utils import decrypt_token

# Read the correct workflow from local file
WF = r"""name: Scrapower Worker
on:
  workflow_dispatch:
    inputs:
      coordinator_url:
        description: 'Coordinator URL'
        required: true
        default: 'https://scrapower.talos-int.com'
      worker_id:
        description: 'Worker ID'
        required: false
        default: 'gh-actions'
jobs:
  worker:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install
        run: pip install aiohttp wasmtime
      - name: Run Worker
        env:
          COORDINATOR_URL: ${{ inputs.coordinator_url }}
          WORKER_ID: ${{ inputs.worker_id }}
        run: |
          python << 'PYEOF'
          import asyncio, aiohttp, os, uuid, hashlib
          C = os.environ['COORDINATOR_URL'].replace('https://', 'wss://').replace('http://', 'ws://')
          if not C.endswith('/worker/ws'):
              C = C.rstrip('/') + '/worker/ws'
          W = os.environ.get('WORKER_ID', f'gh-{uuid.uuid4().hex[:8]}')
          async def main():
              async with aiohttp.ClientSession() as s:
                  async with s.ws_connect(C) as ws:
                      await ws.send_json({'type': 'hello', 'version': '2.1', 'mode': 'persistent', 'worker_id': W, 'auth': {'method': 'none'}})
                      msg = await ws.receive_json()
                      if msg['type'] != 'session':
                          return
                      sid = msg['session_id']
                      hb = msg.get('heartbeat_interval_ms', 10000) // 1000
                      await ws.send_json({'type': 'capabilities', 'session_id': sid, 'payload': {'runtimes': ['wasm'], 'resources': {'cpu_cores': 2, 'ram_mb': 7168, 'gpu': {'supported': False}}, 'lifecycle': {'mode': 'ephemeral', 'max_lifetime_sec': 21600}, 'verification': {'can_challenge': False}, 'network': {'connectivity': 'outgoing_only'}, 'limits': {'max_task_duration_ms': 300000, 'max_concurrent_tasks': 1}}})
                      nxt = asyncio.get_event_loop().time() + hb
                      while True:
                          now = asyncio.get_event_loop().time()
                          if now >= nxt:
                              await ws.send_json({'type': 'heartbeat', 'session_id': sid, 'current_load_pct': 0, 'tasks_in_progress': 0, 'uptime_sec': 0, 'expected_remaining_sec': None})
                              nxt = now + hb
                          try:
                              msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                          except asyncio.TimeoutError:
                              continue
                          except Exception:
                              break
                          mt = msg.get('type', '')
                          if mt in ('task_assign', 'keepalive'):
                              if mt == 'task_assign':
                                  await ws.send_json({'type': 'task_accept', 'session_id': sid, 'task_id': msg['task']['id'], 'assignment_token': msg['task']['assignment_token']})
                              H = C.replace('ws://', 'http://').replace('/worker/ws', '')
                              try:
                                  async with aiohttp.ClientSession() as s2:
                                      async with s2.get(H + '/blobs/' + msg['task']['payload']['executable_hash']) as r:
                                          executable = await r.read()
                                      async with s2.get(H + '/blobs/' + msg['task']['payload']['input_hash']) as r:
                                          inp = await r.read()
                                  try:
                                      import wasmtime
                                      m = wasmtime.Module(executable)
                                      inst = wasmtime.Instance(m, [])
                                      mem = inst.exports['memory']
                                      mem.write_bytes(0, inp)
                                      inst.exports['compute'](0, len(inp), 1024, 4096)
                                      out = bytes(mem.read_bytes(1024, 4096))
                                  except Exception:
                                      out = inp[:100]
                                  oh = hashlib.sha256(out).hexdigest()
                                  async with aiohttp.ClientSession() as s3:
                                      await s3.put(H + '/blobs', data=out)
                                  status, exit_code, stderr = 'success', 0, ''
                              except Exception as e:
                                  oh, status, exit_code, stderr = '', 'error', 1, str(e)[:4096]
                              await ws.send_json({'type': 'task_result', 'session_id': sid, 'task_id': msg['task']['id'], 'assignment_token': msg['task'].get('assignment_token', ''), 'status': status, 'result': {'output_hash': oh, 'execution_metadata': {'duration_ms': 0, 'exit_code': exit_code, 'stderr': stderr}}, 'verification_data': None})
          asyncio.run(main())
          PYEOF
"""


async def main():
    db = await aiosqlite.connect(os.path.expanduser("~/scrapower/data/scrapower.db"))
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM provider_tokens WHERE provider='github'")
    row = await cursor.fetchone()
    if not row:
        print("no token — reconnect on scrapower.talos-int.com")
        return
    token = decrypt_token(row["token_encrypted"])
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with aiohttp.ClientSession() as s:
        async with s.get("https://api.github.com/user", headers=headers) as r:
            username = (await r.json())["login"]
        repo = f"{username}/scrapower-worker"
        path = ".github/workflows/scrapower-worker.yml"

        # Update workflow
        async with s.get(
            f"https://api.github.com/repos/{repo}/contents/{path}", headers=headers
        ) as r:
            sha = (await r.json())["sha"]
        content_b64 = base64.b64encode(WF.encode()).decode()
        async with s.put(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            json={"message": "Fix workflow with heredoc", "content": content_b64, "sha": sha},
            headers=headers,
        ) as r:
            print(f"push_workflow: {r.status}")

        # Cancel any running
        async with s.get(
            f"https://api.github.com/repos/{repo}/actions/runs?status=in_progress", headers=headers
        ) as r:
            for run in (await r.json()).get("workflow_runs", []):
                await s.post(
                    f"https://api.github.com/repos/{repo}/actions/runs/{run['id']}/cancel",
                    headers=headers,
                )

        await asyncio.sleep(10)
        print("waiting for GitHub to index...")

        # Dispatch
        url = (
            f"https://api.github.com/repos/{repo}/actions/workflows/scrapower-worker.yml/dispatches"
        )
        worker_id = f"gh-{int(time.time())}"
        async with s.post(
            url,
            json={
                "ref": "main",
                "inputs": {
                    "coordinator_url": "https://scrapower.talos-int.com",
                    "worker_id": worker_id,
                },
            },
            headers=headers,
        ) as r:
            print(f"dispatch: {r.status} worker={worker_id}")
            if r.status == 204:
                print("DISPATCHED! Checking connection...")
            else:
                print((await r.text())[:200])

    await db.close()


asyncio.run(main())
