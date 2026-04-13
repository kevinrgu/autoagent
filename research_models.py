"""
research_models.py — Benchmark all candidate models for profile assignment.
Warmup + 3-call average tok/s for each candidate.
"""

import httpx
import json
import time

PROMPT = (
    "Write a Python function that merges two sorted lists into one sorted list. "
    "Include docstring and type hints."
)

CANDIDATES = [
    # Bifrost (RDNA4 Vulkan)
    {"name": "mistral-small3.1:24b", "url": "http://127.0.0.1:11434", "node": "Bifrost"},
    {"name": "bifrost-t1b:latest",   "url": "http://127.0.0.1:11434", "node": "Bifrost"},
    {"name": "qwen2.5-coder:14b",    "url": "http://127.0.0.1:11434", "node": "Bifrost"},
    {"name": "qwen3:14b",            "url": "http://127.0.0.1:11434", "node": "Bifrost"},
    {"name": "gemma4:e4b",           "url": "http://127.0.0.1:11434", "node": "Bifrost"},
    # Forge
    {"name": "bifrost-t2-gemma4:latest", "url": "http://192.168.2.50:11434", "node": "Forge"},
    {"name": "gemma4:e4b",           "url": "http://192.168.2.50:11434", "node": "Forge"},
    {"name": "llama4:scout",         "url": "http://192.168.2.50:11434", "node": "Forge"},
    # Hearth
    {"name": "gemma4:e4b",           "url": "http://192.168.2.4:11434", "node": "Hearth"},
    {"name": "qwen2.5-coder:7b",     "url": "http://192.168.2.4:11434", "node": "Hearth"},
    {"name": "qwen3:8b",             "url": "http://192.168.2.4:11434", "node": "Hearth"},
    {"name": "mistral:7b",           "url": "http://192.168.2.4:11434", "node": "Hearth"},
]


def bench_one(candidate: dict) -> dict:
    name = candidate["name"]
    url = candidate["url"]
    node = candidate["node"]
    endpoint = f"{url}/v1/chat/completions"

    result = {
        "name": name,
        "node": node,
        "url": url,
        "available": False,
        "avg_tok_s": 0.0,
        "runs": [],
    }

    payload = {
        "model": name,
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": PROMPT},
        ],
        "stream": False,
        "temperature": 0.7,
    }

    # Warmup
    print(f"  [{node}] {name} — warmup ...", end="", flush=True)
    try:
        r = httpx.post(endpoint, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        warmup_tokens = data.get("usage", {}).get("completion_tokens", 0)
        print(f" OK ({warmup_tokens} tokens)")
    except Exception as e:
        print(f" UNAVAILABLE: {e}")
        return result

    result["available"] = True

    # 3 timed runs
    tok_rates = []
    for i in range(3):
        t0 = time.time()
        try:
            r = httpx.post(endpoint, json=payload, timeout=120)
            r.raise_for_status()
            data = r.json()
            elapsed = time.time() - t0
            tokens = data.get("usage", {}).get("completion_tokens", 0)
            rate = tokens / elapsed if elapsed > 0 else 0
            tok_rates.append(rate)
            result["runs"].append({"tokens": tokens, "elapsed_s": round(elapsed, 2), "tok_s": round(rate, 1)})
            print(f"    run {i+1}: {tokens} tok in {elapsed:.1f}s = {rate:.1f} tok/s")
        except Exception as e:
            print(f"    run {i+1}: FAIL — {e}")
            result["runs"].append({"error": str(e)})

    if tok_rates:
        result["avg_tok_s"] = round(sum(tok_rates) / len(tok_rates), 1)
        print(f"    AVG: {result['avg_tok_s']} tok/s")

    return result


def main():
    print("=" * 60)
    print("BIFROST Model Benchmark")
    print("=" * 60)

    results = []
    for c in CANDIDATES:
        print()
        r = bench_one(c)
        results.append(r)

    # Save results
    with open("research_benchmark.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Model':<35} {'Node':<10} {'Avail':<8} {'Avg tok/s'}")
    print("-" * 70)
    for r in results:
        avail = "YES" if r["available"] else "NO"
        tok = f"{r['avg_tok_s']:.1f}" if r["available"] else "—"
        print(f"{r['name']:<35} {r['node']:<10} {avail:<8} {tok}")

    print(f"\nResults saved to research_benchmark.json")


if __name__ == "__main__":
    main()
