"""Where decks, their images and exported .apkg files are kept.

On Vercel (BLOB_READ_WRITE_TOKEN is set) everything goes to Vercel Blob so the
whole group shares one library. Locally it goes to data/store/ and is served
by the app at /store/...
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = ROOT / "data" / "store"


class LocalStore:
    def put(self, path: str, data: bytes, content_type: str) -> str:
        dest = LOCAL_DIR / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return "/store/" + path

    def get(self, path: str) -> bytes:
        return (LOCAL_DIR / path).read_bytes()

    def list(self, prefix: str) -> List[Dict]:
        items = []
        for f in LOCAL_DIR.rglob("*"):
            path = f.relative_to(LOCAL_DIR).as_posix()
            if f.is_file() and path.startswith(prefix):
                items.append({"path": path, "uploaded_at": f.stat().st_mtime})
        return items


class BlobStore:
    def __init__(self):
        from vercel.blob import BlobClient  # only installed where Python >= 3.10
        self.client = BlobClient()

    def put(self, path: str, data: bytes, content_type: str) -> str:
        result = self.client.put(path, data, access="public", content_type=content_type,
                                 add_random_suffix=False, overwrite=True)
        return result.url

    def get(self, path: str) -> bytes:
        # Files are never overwritten (see main.py), so the CDN copy is always current.
        return self.client.get(path, access="public").content

    def list(self, prefix: str) -> List[Dict]:
        items = []
        for blob in self.client.iter_objects(prefix=prefix):
            items.append({"path": blob.pathname, "uploaded_at": blob.uploaded_at.timestamp()})
        return items


def get_store():
    if os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID"):
        return BlobStore()
    return LocalStore()
