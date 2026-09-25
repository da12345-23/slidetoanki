"""Where decks, their images and exported .apkg files are kept.

On Vercel (BLOB_READ_WRITE_TOKEN is set) everything goes to Vercel Blob so the
whole group shares one library. Locally it goes to data/store/ and is served
by the app at /store/...
"""
from __future__ import annotations

import os
import urllib.request
import uuid
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
        base = LOCAL_DIR / prefix
        if not base.exists():
            return []
        items = []
        for f in base.iterdir():
            if f.is_file():
                items.append({"path": prefix + f.name, "uploaded_at": f.stat().st_mtime})
        return items


class BlobStore:
    def __init__(self):
        from vercel.blob import BlobClient  # only installed where Python >= 3.10
        self.client = BlobClient()

    def put(self, path: str, data: bytes, content_type: str) -> str:
        result = self.client.put(path, data, access="public", content_type=content_type,
                                 add_random_suffix=False, overwrite=True,
                                 cache_control_max_age=60)
        return result.url

    def get(self, path: str) -> bytes:
        # The SDK's no-cache flag (?cache=0) is rejected by public stores, so skip
        # the CDN copy with a unique query instead: edits must show up right away.
        url = self.client.head(path).url
        with urllib.request.urlopen(f"{url}?v={uuid.uuid4().hex}", timeout=30) as res:
            return res.read()

    def list(self, prefix: str) -> List[Dict]:
        items = []
        for blob in self.client.iter_objects(prefix=prefix):
            items.append({"path": blob.pathname, "uploaded_at": blob.uploaded_at.timestamp()})
        return items


def get_store():
    if os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID"):
        return BlobStore()
    return LocalStore()
