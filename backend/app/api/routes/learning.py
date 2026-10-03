"""Read-only learning catalog and bounded, allowlisted source excerpts."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from app.services.learning_catalog import SOURCES, get_catalog

router = APIRouter(prefix="/learning", tags=["learning"])
ROOT = Path(__file__).resolve().parents[4]
MAX_SOURCE_BYTES = 256 * 1024


@router.get("/catalog")
def catalog():
    return get_catalog()


@router.get("/source/{source_id}")
def source_excerpt(source_id: str, response: Response):
    spec = SOURCES.get(source_id)
    if spec is None:
        raise HTTPException(404, "课程源码不存在")
    relative_path, anchor, count = spec
    path = (ROOT / relative_path).resolve()
    # A replaced file or symlink must never escape the known repository.
    expected_path = ROOT.resolve() / relative_path
    if (path != expected_path or not path.is_relative_to(ROOT.resolve())
            or path.suffix not in {".py", ".ts", ".tsx"}):
        raise HTTPException(404, "课程源码不可用")
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("oversized")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError, ValueError):
        raise HTTPException(404, "当前部署未包含这份课程源码") from None
    if anchor:
        matches = [index for index, line in enumerate(lines) if line.startswith(anchor)]
        if not matches:
            raise HTTPException(409, "源码已更新，课程定位需要刷新")
        start = matches[0]
    else:
        start = 0
    response.headers["Cache-Control"] = "no-store"
    return {"id": source_id, "path": relative_path, "start_line": start + 1,
            "end_line": min(start + count, len(lines)),
            "code": "\n".join(lines[start:start + min(count, 140)]),
            "notice": "只读当前服务部署的固定源码片段，不包含配置、凭证或用户资料；不会发送给模型。"}
