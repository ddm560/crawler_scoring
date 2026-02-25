import json

with open("features.jsonl", "r", encoding="utf-8") as f:
    rows = [json.loads(line) for line in f if line.strip()]

print(rows[0])
