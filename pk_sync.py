"""
pk_sync.py — Copy reports, decisions, and config files to pk-upload staging dir.
"""
import shutil
import glob
import os
from datetime import datetime
from pathlib import Path

AUTOAGENT_DIR = Path(r"C:\Users\jhpri\projects\autoagent")
BIFROST_DIR = Path(r"D:\Projects\bifrost-router")
DEST_DIR = Path(r"L:\temp\pk-upload")
PROFILES = ["CODING", "GENERAL", "RESEARCH"]


def sync():
    # Check root drive is accessible before trying to create subdirs
    dest_root = Path(DEST_DIR.anchor)
    if not dest_root.exists():
        print(f"[pk_sync] WARN: destination drive {dest_root} not available — skipping sync.")
        return [], []
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    copied = []
    skipped = []

    def copy_file(src: Path, label: str = ""):
        src = Path(src)
        if src.exists():
            dest = DEST_DIR / src.name
            shutil.copy2(src, dest)
            tag = label or str(src)
            copied.append(tag or str(src))
            print(f"  [OK] {src.name}")
        else:
            skipped.append(str(src))
            print(f"  [--] {src} (not found)")

    print("=== pk_sync ===")

    # 1. All *REPORT*.md from autoagent root
    print("\n[1] REPORT*.md files")
    report_files = list(AUTOAGENT_DIR.glob("*REPORT*.md"))
    if report_files:
        for f in report_files:
            copy_file(f)
    else:
        print("  (none found)")

    # 2. Per-profile decisions.md
    print("\n[2] Profile decisions.md")
    for profile in PROFILES:
        src = AUTOAGENT_DIR / "profiles" / profile / "decisions.md"
        if src.exists():
            dest_name = f"decisions_{profile}.md"
            shutil.copy2(src, DEST_DIR / dest_name)
            copied.append(f"profiles/{profile}/decisions.md -> {dest_name}")
            print(f"  [OK] profiles/{profile}/decisions.md -> {dest_name}")
        else:
            skipped.append(str(src))
            print(f"  [--] profiles/{profile}/decisions.md (not found)")

    # 3. autoagent root files
    print("\n[3] autoagent root files")
    for fname in ["active_profile.txt", "profiles.json"]:
        copy_file(AUTOAGENT_DIR / fname, fname)

    # 4. bifrost-router files
    print("\n[4] bifrost-router files")
    for fname in ["fleet_config.json", "main.py", "config.py"]:
        src = BIFROST_DIR / fname
        if src.exists():
            dest_name = f"bifrost_{fname}"
            shutil.copy2(src, DEST_DIR / dest_name)
            copied.append(f"bifrost-router/{fname} -> {dest_name}")
            print(f"  [OK] {fname} -> {dest_name}")
        else:
            skipped.append(str(src))
            print(f"  [--] {src} (not found)")

    # 5. Write manifest
    manifest_path = DEST_DIR / "pk_sync_manifest.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(manifest_path, "w") as f:
        f.write(f"pk_sync run: {ts}\n\n")
        f.write("Copied:\n")
        for item in copied:
            f.write(f"  + {item}\n")
        if skipped:
            f.write("\nSkipped (not found):\n")
            for item in skipped:
                f.write(f"  - {item}\n")
    print(f"\n[manifest] {manifest_path}")
    print(f"\nDone: {len(copied)} copied, {len(skipped)} skipped.")
    return copied, skipped


if __name__ == "__main__":
    sync()
