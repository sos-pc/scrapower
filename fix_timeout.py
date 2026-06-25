import json

with open("deploy/kaggle/sworker.ipynb", "r", encoding="utf-8") as f:
    nb = json.load(f)

for cell in nb["cells"]:
    src = cell.get("source", "")
    if "total=120" in src and "blobs" in src:
        cell["source"] = src.replace(
            "timeout=aiohttp.ClientTimeout(total=120)",
            "timeout=aiohttp.ClientTimeout(total=min(300, max(30, 10 + len(output) // 50_000)))",
        )
        print("Fixed Kaggle: adaptive upload timeout")
        break

with open("deploy/kaggle/sworker.ipynb", "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
    f.write("\n")
