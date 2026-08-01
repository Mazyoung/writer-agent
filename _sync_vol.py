import sys; sys.path.insert(0, ".")
from src.core.orchestrator import Orchestrator

orch = Orchestrator("nuclear_cultivation")

texts = []
for si in range(1, 10):
    t = orch.file_store.load_latest("chapters", f"scene_ch0001_s{si:02d}")
    if t:
        texts.append(t)
    else:
        break
ch1_text = "\n\n".join(texts)
print(f"Assembled chapter from {len(texts)} scenes ({len(ch1_text)} chars)")

digest = orch._load_fact_digest(1)
if digest:
    print(f"Fact digest found ({len(digest)} chars), re-running volume outline update...")
    orch._replan_volume_outline(1, ch1_text)
    print("Volume outline re-synced.")
else:
    print("Fact digest still missing!")
