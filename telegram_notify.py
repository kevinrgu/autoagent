"""telegram_notify.py - Send overnight run summary to Telegram.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env. No-op if either is missing.
"""
from __future__ import annotations

import os
import urllib.parse
import urllib.request
from typing import Optional


def _get_credentials() -> tuple[Optional[str], Optional[str]]:
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send_message(text: str, *, parse_mode: str = "Markdown", timeout: float = 10.0) -> bool:
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  telegram_notify failed: {e}")
        return False


def format_overnight_summary(run_stamp: str, results: list[dict], log_lines: list[str]) -> str:
    lines = [f"*Overnight run* `{run_stamp}`", ""]
    for r in results:
        status = "TIMEOUT" if r.get("timed_out") else ("OK" if r.get("returncode") == 0 else "FAIL")
        lines.append(f"`{r['profile']:<10} {status:<7} {r['elapsed_s']:>7.1f}s`")
    if log_lines:
        lines.append("")
        for line in log_lines:
            lines.append(f"_{line}_")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ok = send_message(" ".join(sys.argv[1:]) or "telegram_notify test")
    print("sent" if ok else "skipped/failed")
