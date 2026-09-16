#!/usr/bin/env python3
"""Web service for the FIT merge page."""

from __future__ import annotations

import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, request, send_file, send_from_directory

ROOT = Path(__file__).resolve().parent
TOOL_DIR = ROOT / "fit_merge_tool"
sys.path.insert(0, str(TOOL_DIR))

from merge import MERGE_FUNCTIONS, manufacturer  # noqa: E402

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
MAX_FILES = 10


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/health")
@app.get("/api/index")
def health():
    return {"status": "ok"}


@app.post("/merge")
@app.post("/api/index")
def merge_upload():
    expected_token = os.getenv("ACCESS_TOKEN")
    if expected_token and request.form.get("token") != expected_token:
        abort(403)

    uploads = [item for item in request.files.getlist("files") if item.filename]
    if len(uploads) < 2:
        return "至少需要两个 FIT 文件", 400
    if len(uploads) > MAX_FILES:
        return f"一次最多上传 {MAX_FILES} 个 FIT 文件", 400

    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for index, upload in enumerate(uploads):
            name = Path(upload.filename or "").name
            if Path(name).suffix.lower() != ".fit":
                return f"不支持文件：{name}", 400
            path = Path(directory) / f"{index:02d}.fit"
            upload.save(path)
            paths.append(path)

        try:
            brands = {manufacturer(path) for path in paths}
            if len(brands) != 1:
                raise ValueError("所有文件必须来自同一品牌")
            brand = brands.pop()
            output = Path(directory) / "merged.fit"
            MERGE_FUNCTIONS[brand](paths, output)
            payload = output.read_bytes()
        except Exception as error:
            return str(error), 400

    filename = "fit_coros_to_garmin.fit" if brand == "coros" else "fit_garmin_to_coros.fit"
    return send_file(
        BytesIO(payload),
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
