#!/usr/bin/env python3
"""
bridge_to_kb.py — Paperless post-consume script
Deploys to: L:\docker\paperless\scripts\bridge_to_kb.py on Hearth

Reads Paperless env vars, queries tags to determine project,
then uploads the document to bifrost-kb with the correct project.
"""
import os
import sys
import httpx

# --- Config ---
PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "http://localhost:8000")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")
KB_URL = os.environ.get("BIFROST_KB_URL", "http://bifrost-kb:8100")

# Tag-to-project mapping
TAG_PROJECT_MAP = {
    "personal": "personal",
    "client": "client",
    "rfp": "rfp",
    "rfp-vault": "rfp",
}
DEFAULT_PROJECT = "default"

# Paperless post-consume env vars
DOCUMENT_ID = os.environ.get("DOCUMENT_ID")
DOCUMENT_SOURCE_PATH = os.environ.get("DOCUMENT_SOURCE_PATH", "")
DOCUMENT_ORIGINAL_FILENAME = os.environ.get("DOCUMENT_ORIGINAL_FILENAME", "")


def get_project_from_tags(doc_id: str) -> str:
    """Query Paperless API for document tags, map to bifrost-kb project."""
    if not PAPERLESS_TOKEN:
        print("[bridge] WARN: no PAPERLESS_TOKEN set, using default project")
        return DEFAULT_PROJECT

    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}

    try:
        # Get document details
        resp = httpx.get(
            f"{PAPERLESS_URL}/api/documents/{doc_id}/",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        doc = resp.json()
        tag_ids = doc.get("tags", [])

        if not tag_ids:
            return DEFAULT_PROJECT

        # Get tag names
        for tid in tag_ids:
            tag_resp = httpx.get(
                f"{PAPERLESS_URL}/api/tags/{tid}/",
                headers=headers,
                timeout=10,
            )
            if tag_resp.status_code == 200:
                tag_name = tag_resp.json().get("name", "").lower()
                if tag_name in TAG_PROJECT_MAP:
                    return TAG_PROJECT_MAP[tag_name]

        return DEFAULT_PROJECT

    except Exception as e:
        print(f"[bridge] WARN: tag lookup failed ({e}), using default project")
        return DEFAULT_PROJECT


def get_project_from_path(source_path: str) -> str:
    """Fallback: infer project from source path subfolder."""
    path_lower = source_path.lower().replace("\\", "/")
    for folder, project in [
        ("personal/", "personal"),
        ("client/", "client"),
        ("rfp-vault/", "rfp"),
    ]:
        if folder in path_lower:
            return project
    return DEFAULT_PROJECT


def upload_to_kb(filepath: str, project: str, filename: str):
    """Upload file to bifrost-kb /upload endpoint."""
    print(f"[bridge] Uploading {filename} to bifrost-kb project={project}")
    try:
        with open(filepath, "rb") as f:
            resp = httpx.post(
                f"{KB_URL}/upload",
                files={"file": (filename, f)},
                data={"project": project},
                timeout=60,
            )
        if resp.status_code in (200, 201):
            print(f"[bridge] OK: {resp.json()}")
        else:
            print(f"[bridge] WARN: KB returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[bridge] ERROR: upload failed: {e}")


def main():
    if not DOCUMENT_ID:
        print("[bridge] No DOCUMENT_ID env var — not a post-consume call, exiting.")
        sys.exit(0)

    source_path = DOCUMENT_SOURCE_PATH
    filename = DOCUMENT_ORIGINAL_FILENAME or os.path.basename(source_path)

    # Determine project: tags first, then path fallback
    project = get_project_from_tags(DOCUMENT_ID)
    if project == DEFAULT_PROJECT:
        path_project = get_project_from_path(source_path)
        if path_project != DEFAULT_PROJECT:
            project = path_project

    print(f"[bridge] doc_id={DOCUMENT_ID} file={filename} project={project}")

    # Find the actual file to upload (archived version preferred)
    archive_path = os.environ.get("DOCUMENT_ARCHIVE_PATH", "")
    upload_path = archive_path if archive_path and os.path.exists(archive_path) else source_path

    if not upload_path or not os.path.exists(upload_path):
        print(f"[bridge] ERROR: no file found at {upload_path}")
        sys.exit(1)

    upload_to_kb(upload_path, project, filename)


if __name__ == "__main__":
    main()
