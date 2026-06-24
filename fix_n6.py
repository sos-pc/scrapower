import sys

src = open(sys.argv[1], "rb").read()

# Find the wg_proxy injection block
idx = src.find(b'wg_proxy = os.environ.get("SCRAPOWER_WG_PROXY_PUBLIC"')
if idx < 0:
    print("FAIL find")
    sys.exit(1)

# Find end of the if block (next non-indented line)
end = src.find(b'\n            cell["source"]', idx)
if end < 0:
    print("FAIL find end")
    sys.exit(1)

new = b"""            wg_proxy = os.environ.get("SCRAPOWER_WG_PROXY_PUBLIC", "") or os.environ.get(
                "SCRAPOWER_WG_PROXY", ""
            )
            if wg_proxy:
                # Never put the full proxy URL (with password) in the notebook source.
                # Inject components separately, assembled at runtime by the worker.
                try:
                    rest = wg_proxy.split("://", 1)[1]
                    auth, host_port = rest.split("@", 1)
                    user, passwd = auth.split(":", 1)
                    host = host_port.rsplit(":", 1)[0]
                except (ValueError, IndexError):
                    user, passwd, host = "scrapower", "", "scrapower.talos-int.com"
                src = src.replace('WG_USER = ""', f'WG_USER = "{user}"')
                src = src.replace('WG_PASS = ""', f'WG_PASS = "{passwd}"')
                src = src.replace('WG_HOST = ""', f'WG_HOST = "{host}"')
"""
src = src[:idx] + new + src[end:]
print("1. kaggle harvester updated")

open(sys.argv[1], "wb").write(src)
print("DONE")
