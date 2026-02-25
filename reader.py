import pandas as pd

df = pd.read_json("features.jsonl", lines=True)
print(df[["input_domain", "score", "bucket", "confidence"]].sort_values("score"))
