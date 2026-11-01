"""Provider-neutral asset/API transfers for the operation broker (stdlib only)."""
import hashlib
import json
import mimetypes
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from .storage import require_space

COMMANDS = {"download_asset", "upload_asset", "request_asset_api"}


def private_summary(params):
    # Credentials, signed URLs and API request bodies never enter the journal.
    return {key: params[key] for key in ("source", "path", "method", "expected_sha256", "overwrite") if key in params}


def transfer(command, params, progress, cancelled):
    url = params["url"]
    origin = urllib.parse.urlsplit(url)[:2]

    class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            if urllib.parse.urlsplit(newurl)[:2] != origin:
                raise ValueError("Refusing an asset redirect to a different origin")
            return super().redirect_request(request, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(SameOriginRedirect())
    headers = dict(params.get("headers", {}))
    timeout, limit = params.get("timeout_seconds", 120), params.get("max_bytes", 256 * 1024 * 1024)
    path = Path(params["path"]) if params.get("path") else None
    if command == "download_asset":
        if path.exists() and not params.get("overwrite", False):
            raise FileExistsError(path)
        fd, temporary = tempfile.mkstemp(prefix=".asset-", dir=path.parent)
        try:
            digest, total = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as output, opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
                while True:
                    if cancelled():
                        raise InterruptedError("Asset download cancelled")
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("Asset exceeds configured maximum size")
                    storage = params.get("_storage_policy", {})
                    require_space(path, len(chunk), reserve_bytes=storage.get("reserve_bytes", 256 * 1024**2))
                    output.write(chunk)
                    digest.update(chunk)
                    progress({"phase": "downloading", "bytes_received": total})
                output.flush()
                os.fsync(output.fileno())
            checksum = digest.hexdigest()
            if params.get("expected_sha256") and checksum != params["expected_sha256"].lower():
                raise ValueError("Asset SHA-256 mismatch")
            if params.get("overwrite", False):
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
                os.unlink(temporary)
            return {"path": str(path), "bytes": total, "sha256": checksum, "source": params["source"]}
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if cancelled():
        raise InterruptedError("API operation cancelled before sending")
    if command == "upload_asset":
        if path.stat().st_size > limit:
            raise ValueError("Asset exceeds configured maximum size")
        headers.setdefault("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        headers["Content-Length"] = str(path.stat().st_size)
        progress({"phase": "uploading", "input_bytes": path.stat().st_size})
        with path.open("rb") as stream:
            request = urllib.request.Request(url, data=stream, headers=headers, method=params.get("method", "PUT"))
            with opener.open(request, timeout=timeout) as response:
                body = response.read(1024 * 1024 + 1)
                status = response.status
    else:
        data = json.dumps(params["payload"]).encode() if params.get("payload") is not None else None
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=headers, method=params.get("method", "GET"))
        with opener.open(request, timeout=timeout) as response:
            body = response.read(1024 * 1024 + 1)
            status = response.status
    if len(body) > 1024 * 1024:
        raise ValueError("API response exceeds 1 MiB")
    try:
        value = json.loads(body) if body else None
    except ValueError:
        value = body.decode(errors="replace")
    return {"status_code": status, "response": value, "source": params["source"]}
