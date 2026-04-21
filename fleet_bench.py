"""fleet_bench.py - 7-endpoint fleet benchmark.

Sequential calls (max_tokens=300) across all active tier endpoints.
Writes fleet_bench.json to the working directory.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import httpx

PROMPT = (
    "Write a Python function that reads a JSON file, validates that it "
    "contains a name and value field, and returns a dict. Include type "
    "hints and docstring. Return only the code."
)
MAX_TOKENS = 300

# (slot, host, model, endpoint, backend)
ENDPOINTS = [
    ("T1-Bifrost",  "192.168.2.33", "gemma4:e4b",                "http://192.168.2.33:11434/v1/chat/completions", "ollama"),
    ("T1-Hearth",   "192.168.2.4",  "mistral:latest",            "http://192.168.2.4:11434/v1/chat/completions",  "ollama"),
    ("T1-Forge",    "192.168.2.50", "gemma4:e4b",                "http://192.168.2.50:11434/v1/chat/completions", "ollama"),
    ("T2-Forge",    "192.168.2.50", "bifrost-t2-gemma4",         "http://192.168.2.50:11434/v1/chat/completions", "ollama"),
    ("T2.5-Forge",  "192.168.2.50", "llama3.3:70b",              "http://192.168.2.50:11438/v1/chat/completions", "llama-server"),
    ("NPU-Forge",   "192.168.2.50", "qwen3:1.7b",                "http://192.168.2.50:8003/v1/chat/completions",  "flm"),
    ("3-Fast",      "api.anthropic.com", "claude-haiku-4-5-20251001", "https://api.anthropic.com/v1/messages",     "anthropic"),
]


def _call(ep: tuple) -> dict:
    slot, host, model, url, backend = ep
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    t0 = time.time()
    try:
        with httpx.Client(timeout=300.0) as c:
            if backend == "anthropic":
                # Skip unless API key available.
                import os
                key = os.environ.get("ANTHROPIC_API_KEY")
                if not key:
                    return {"slot": slot, "host": host, "model": model, "skipped": True, "reason": "no ANTHROPIC_API_KEY"}
                headers = {
                    "Content-Type": "application/json",
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                }
                ab = {
                    "model": model,
                    "max_tokens": MAX_TOKENS,
                    "messages": [{"role": "user", "content": PROMPT}],
                }
                r = c.post(url, json=ab, headers=headers)
                r.raise_for_status()
                j = r.json()
                text = "".join(blk.get("text", "") for blk in j.get("content", []) if blk.get("type") == "text")
                wall = time.time() - t0
                tokens = j.get("usage", {}).get("output_tokens", 0)
                return {
                    "slot": slot, "host": host, "model": model, "backend": backend,
                    "wall_s": round(wall, 2),
                    "tokens": tokens,
                    "tok_s": round(tokens / wall, 2) if wall > 0 else 0,
                    "preview": text[:140],
                }
            r = c.post(url, json=body, headers=headers)
            r.raise_for_status()
            j = r.json()
            wall = time.time() - t0
            text = j.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            tokens = j.get("usage", {}).get("completion_tokens", 0)
            return {
                "slot": slot, "host": host, "model": model, "backend": backend,
                "wall_s": round(wall, 2),
                "tokens": tokens,
                "tok_s": round(tokens / wall, 2) if wall > 0 else 0,
                "preview": text[:140],
            }
    except Exception as e:
        return {
            "slot": slot, "host": host, "model": model, "backend": backend,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "wall_s": round(time.time() - t0, 2),
        }


def main() -> None:
    started = datetime.now().isoformat(timespec="seconds")
    results = []
    for ep in ENDPOINTS:
        slot = ep[0]
        print(f"[{slot}] ...", flush=True)
        res = _call(ep)
        print(f"  {json.dumps(res)[:300]}", flush=True)
        results.append(res)
    out = {
        "started": started,
        "finished": datetime.now().isoformat(timespec="seconds"),
        "prompt": PROMPT,
        "max_tokens": MAX_TOKENS,
        "results": results,
    }
    path = Path("fleet_bench.json")
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {path.resolve()}")

    print("\n--- Summary ---")
    print(f"{'Slot':<12} {'Wall(s)':>8} {'Tokens':>7} {'tok/s':>7}  Model")
    for r in results:
        slot = r.get("slot", "?")
        wall = r.get("wall_s", "-")
        tokens = r.get("tokens", "-") if not r.get("error") else "ERR"
        toks = r.get("tok_s", "-")
        model = r.get("model", "?")
        print(f"{slot:<12} {str(wall):>8} {str(tokens):>7} {str(toks):>7}  {model}")


if __name__ == "__main__":
    main()
