"""Patch kagglesdk: TimeDeltaSerializer crashes on values like "0s" (no ".").

Fails loudly if the upstream source no longer matches — a silent no-op would
reintroduce the original crash at runtime with nothing in the build log.
"""

import sys

path = sys.argv[1]
with open(path) as f:
    c = f.read()

old = '(seconds, nanosRaw) = value.rstrip("s").split(".")'
new = (
    'parts = value.rstrip("s").split("."); seconds = parts[0]; '
    'nanosRaw = parts[1] if len(parts) > 1 else "0"'
)

if new in c:
    print("kagglesdk patch already applied - skipping")
    sys.exit(0)

if old not in c:
    sys.exit(
        f"ERROR: kagglesdk patch target not found in {path}.\n"
        "The upstream source changed; re-check the TimeDeltaSerializer fix "
        "before shipping (a silent skip would reintroduce the '0s' crash)."
    )

with open(path, "w") as f:
    f.write(c.replace(old, new))
print("kagglesdk patch applied")
