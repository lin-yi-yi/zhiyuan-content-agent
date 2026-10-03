"""Bounded, text-only document extraction. Never opens paths or fetches URLs.

Binary parsers run in a short-lived child process. PDF extraction follows the
pypdf text extraction contract: images are not OCRed and layout is approximate.
"""
import base64
import binascii
import io
import json
from pathlib import Path
import subprocess
import sys
from threading import BoundedSemaphore
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAX_FILE_BYTES = 1024 * 1024
MAX_CONTENT_CHARS = 120_000
MAX_PDF_PAGES = 50
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_PDF_STREAM_BYTES = 2 * 1024 * 1024
MAX_ENCODED_LENGTH = 4 * ((MAX_FILE_BYTES + 2) // 3)
_PARSERS = BoundedSemaphore(2)


class DocumentParseError(ValueError):
    pass


def _bounded_text(text):
    text = text.replace("\x00", "").strip()
    if len(text) > MAX_CONTENT_CHARS:
        raise DocumentParseError("提取正文超过 120,000 字符，请拆分成几份文档后重试。")
    if len(text) < 40:
        raise DocumentParseError("提取正文不足 40 字符，请检查文件是否有可复制的文字，或改为粘贴正文。")
    return text


def _docx(data):
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(item.file_size for item in entries) > MAX_ZIP_BYTES:
                raise DocumentParseError("Word 文件解压后的内容过大，请移除图片或拆分文档。")
            if any(item.flag_bits & 1 or item.file_size > MAX_XML_BYTES
                   or item.file_size / max(item.compress_size, 1) > 100 for item in entries):
                raise DocumentParseError("Word 文件包含受保护或压缩比例过高的内容，无法安全提取。")
            names = [item.filename for item in entries]
            if len(names) != len(set(names)) or "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise DocumentParseError("不是有效的 DOCX 文档，请在 Word 中另存为 .docx 后重试。")
            raw = archive.read("word/document.xml")
    except (BadZipFile, KeyError, RuntimeError, NotImplementedError) as exc:
        raise DocumentParseError("DOCX 文件损坏、加密或格式不受支持，请重新导出后重试。") from exc
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise DocumentParseError("Word 文件包含不支持的 XML 声明，请重新导出后重试。")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DocumentParseError("Word 正文结构损坏，无法提取。") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs, char_count = [], 0
    for paragraph in root.iter(f"{namespace}p"):
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                pieces.append(node.text)
            elif node.tag == f"{namespace}tab":
                pieces.append("\t")
            elif node.tag in {f"{namespace}br", f"{namespace}cr"}:
                pieces.append("\n")
        text = "".join(pieces).strip()
        char_count += len(text) + 2
        if char_count > MAX_CONTENT_CHARS:
            raise DocumentParseError("提取正文超过 120,000 字符，请拆分成几份文档后重试。")
        if text:
            paragraphs.append(text)
    return {"content": _bounded_text("\n\n".join(paragraphs)), "page_count": None,
            "warnings": ["已提取正文及表格中的文字；图片、批注、页眉页脚未导入，复杂表格布局需要检查。"]}


def _pdf(data):
    if not data.lstrip().startswith(b"%PDF-"):
        raise DocumentParseError("文件内容不是有效 PDF，请检查扩展名或重新导出。")
    try:
        from pypdf import PdfReader, overwrite_configuration
    except ImportError as exc:
        raise DocumentParseError("服务尚未安装 PDF 解析组件，请联系维护者安装 pypdf。") from exc
    # Configure the public 6.19 API in this disposable process. The older
    # ZLIB_MAX_OUTPUT_LENGTH module constant is not the active configuration.
    overwrite_configuration(
        maximum_declared_stream_length=MAX_PDF_STREAM_BYTES,
        array_based_stream_maximum_output_length=MAX_PDF_STREAM_BYTES,
        zlib_maximum_output_length=MAX_PDF_STREAM_BYTES,
        lzw_maximum_output_length=MAX_PDF_STREAM_BYTES,
        run_length_maximum_output_length=MAX_PDF_STREAM_BYTES,
        image_maximum_buffer_size=MAX_PDF_STREAM_BYTES,
        page_tree_maximum_entries=200,
        page_tree_maximum_depth=30,
        xform_maximum_invocations_per_extraction=500,
        disable_legacy_handling=True,
    )
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise DocumentParseError("暂不支持加密 PDF，请移除密码并重新导出后上传。")
        page_count = len(reader.pages)
        if not 1 <= page_count <= MAX_PDF_PAGES:
            raise DocumentParseError("PDF 最多支持 50 页，请拆分后再导入。")
        pieces, empty_pages, chars, stream_bytes = [], [], 0, 0
        for index, page in enumerate(reader.pages, 1):
            stream = page.get_contents()
            if stream is not None:
                size = len(stream.get_data())
                stream_bytes += size
                if size > MAX_PDF_STREAM_BYTES or stream_bytes > MAX_ZIP_BYTES:
                    raise DocumentParseError("PDF 内容流过大，请按页拆分或重新导出简化版本。")
            text = (page.extract_text() or "").strip()
            chars += len(text) + 2
            if chars > MAX_CONTENT_CHARS:
                raise DocumentParseError("提取正文超过 120,000 字符，请拆分成几份文档后重试。")
            if text:
                pieces.append(text)
            else:
                empty_pages.append(index)
        if not pieces:
            raise DocumentParseError("这份 PDF 没有可提取文字，可能是扫描件。本系统暂不支持 OCR，请先识别文字，或粘贴正文。")
        warnings = ["PDF 阅读顺序和复杂表格可能变化，请在确认入库前检查预览；图片中的文字不会被识别。"]
        if empty_pages:
            warnings.append(f"第 {', '.join(map(str, empty_pages))} 页没有可提取文字，可能为空白页或扫描页；这些页未导入，暂不支持 OCR。")
        return {"content": _bounded_text("\n\n".join(pieces)), "page_count": page_count, "warnings": warnings}
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("PDF 无法安全解析，可能损坏或内容结构过于复杂；请重新导出或粘贴正文。") from exc


def _extract(data, extension):
    if extension == ".pdf":
        return _pdf(data)
    if extension == ".docx":
        return _docx(data)
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentParseError("文本文件不是 UTF-8 编码，请另存为 UTF-8 后重试，或直接粘贴正文。") from exc
    return {"content": _bounded_text(content), "page_count": None, "warnings": []}


def parse_document(filename, encoded):
    extension = Path(filename).suffix.lower()
    if extension not in {".md", ".markdown", ".txt", ".pdf", ".docx"}:
        raise DocumentParseError("请选择 Markdown、TXT、PDF 或 DOCX 文件；旧版 .doc 请另存为 .docx。")
    if len(encoded) > MAX_ENCODED_LENGTH:
        raise DocumentParseError("文件超过 1 MiB，请拆分或压缩后再导入。")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DocumentParseError("文件传输内容无效，请重新选择文件。") from exc
    if not data or len(data) > MAX_FILE_BYTES:
        raise DocumentParseError("请选择非空且不超过 1 MiB 的文件。")
    if extension in {".pdf", ".docx"}:
        if not _PARSERS.acquire(blocking=False):
            raise DocumentParseError("已有文档正在提取，请稍后重试。")
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", extension],
                                    input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=12, check=False)
            if result.returncode or len(result.stdout) > MAX_CONTENT_CHARS * 6 + 4096:
                raise DocumentParseError("文件解析超出资源限制，请拆分文档或粘贴正文后重试。")
            output = json.loads(result.stdout)
            if "error" in output:
                raise DocumentParseError(output["error"])
        except subprocess.TimeoutExpired as exc:
            raise DocumentParseError("提取用时过长，已停止处理。请拆分文档或粘贴正文后重试。") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise DocumentParseError("文件解析暂时不可用，请稍后重试或粘贴正文。") from exc
        finally:
            _PARSERS.release()
    else:
        output = _extract(data, extension)
    return {"title": Path(filename.replace("\\", "/")).stem[:200], "content": output["content"],
            "format": "markdown" if extension in {".md", ".markdown"} else "text",
            "file_type": extension[1:], "file_size": len(data), "character_count": len(output["content"]),
            "page_count": output["page_count"], "warnings": output["warnings"]}


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--worker":
    # Keep the large libraries and untrusted parsers outside the web worker.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass
    try:
        payload = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
        if len(payload) > MAX_FILE_BYTES:
            raise DocumentParseError("文件超过 1 MiB，请拆分后重试。")
        result = _extract(payload, sys.argv[2])
    except DocumentParseError as exc:
        result = {"error": str(exc)}
    except Exception:
        result = {"error": "文件无法解析，请重新导出或粘贴正文。"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
