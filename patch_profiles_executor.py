from pathlib import Path
import json, shutil, time

p = Path(r"C:\Users\jhpri\projects\autoagent\profiles.json")
backup = p.with_suffix(f".json.bak-{int(time.time())}")
shutil.copy2(p, backup)
print(f"Backup written: {backup}")

data = json.loads(p.read_text(encoding="utf-8"))
coding = data["profiles"]["coding"]["executor"]

assert coding["model"] == "mistral-7b-v0.3", f"unexpected model: {coding['model']}"
assert coding["url"] == "http://192.168.2.4:11437/v1/chat/completions", f"unexpected url: {coding['url']}"

coding["model"] = "mistral:7b-instruct-v0.3"
coding["url"]   = "http://192.168.2.33:11434/v1/chat/completions"

p.write_text(json.dumps(data, indent=2), encoding="utf-8")
print("OK - CODING executor: bifrost-ollama mistral:7b-instruct-v0.3")
print(f"Rollback: copy {backup} back to {p}")
