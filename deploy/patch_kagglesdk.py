import sys

path = sys.argv[1]
with open(path) as f:
    c = f.read()
old = '(seconds, nanosRaw) = value.rstrip("s").split(".")'
new = 'parts = value.rstrip("s").split("."); seconds = parts[0]; nanosRaw = parts[1] if len(parts) > 1 else "0"'
c = c.replace(old, new)
with open(path, "w") as f:
    f.write(c)
print("kagglesdk patch applied")
