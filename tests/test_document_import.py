"""Synthetic real PDF/DOCX extraction and existing SaaS import boundary regressions."""
import base64
import io
from pathlib import Path
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services import document_parser as parser
from test_saas_isolation import application, teams, invited_member

CONTENT = '这份合成产品资料仅用于验证文档导入。系统先提取正文预览，用户确认后才能建立知识库索引。资料不会自动变成已经人工核验的事实。'


def encoded(data):
    return base64.b64encode(data).decode('ascii')


def docx_bytes(content=CONTENT, *, xml=None, extra=None):
    body = xml or f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{content}</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格中的正文也需要保留。</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'.encode()
    stream = io.BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', body)
        if extra:
            archive.writestr('word/extra.xml', extra)
    return stream.getvalue()


def pdf_bytes(*, pages=1, text=True, encrypted=False, blank_last=False):
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=500, height=500)
        if text and not (blank_last and index == pages-1):
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 12 Tf 40 450 Td (Synthetic document import requires preview and explicit user confirmation before indexing.) Tj ET')
            page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('synthetic-test-only')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_real_pdf_extracts_text_and_marks_layout_limitations():
    result = parser.parse_document('guide.pdf', encoded(pdf_bytes()))
    assert 'explicit user confirmation' in result['content']
    assert result['page_count'] == 1 and result['format'] == 'text'
    assert result['warnings'] and result['character_count'] == len(result['content'])


def test_real_docx_extracts_chinese_paragraph_and_table():
    result = parser.parse_document('产品资料.docx', encoded(docx_bytes()))
    assert CONTENT in result['content'] and '表格中的正文' in result['content']
    assert result['title'] == '产品资料' and result['file_type'] == 'docx'
    assert any('图片' in warning for warning in result['warnings'])


def test_pdf_without_text_fails_explicitly_instead_of_fake_ocr():
    with pytest.raises(parser.DocumentParseError, match='暂不支持 OCR'):
        parser.parse_document('scan.pdf', encoded(pdf_bytes(text=False)))


def test_partial_pdf_warns_which_page_is_not_imported():
    result = parser.parse_document('mixed.pdf', encoded(pdf_bytes(pages=2, blank_last=True)))
    assert result['page_count'] == 2
    assert any('第 2 页' in warning and '未导入' in warning for warning in result['warnings'])


def test_pdf_compressed_content_is_bounded_before_text_extraction():
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=500)
    stream = DecodedStreamObject()
    stream.set_data(b' ' * (parser.MAX_PDF_STREAM_BYTES + 1000))
    page[NameObject('/Contents')] = writer._add_object(stream.flate_encode())
    data = io.BytesIO()
    writer.write(data)
    assert len(data.getvalue()) < parser.MAX_FILE_BYTES
    with pytest.raises(parser.DocumentParseError, match='无法安全解析'):
        parser.parse_document('expanded.pdf', encoded(data.getvalue()))


@pytest.mark.parametrize('kwargs, expected', [({'pages':51}, '最多支持 50 页'), ({'encrypted':True}, '加密 PDF')])
def test_pdf_page_and_encryption_bounds(kwargs, expected):
    with pytest.raises(parser.DocumentParseError, match=expected):
        parser.parse_document('limited.pdf', encoded(pdf_bytes(**kwargs)))


@pytest.mark.parametrize('name,data,expected', [
    ('old.doc', b'not docx', '.docx'),
    ('invalid.pdf', b'not a PDF', '不是有效 PDF'),
    ('broken.docx', b'not a ZIP', '损坏'),
    ('empty.txt', b'', '非空'),
    ('short.txt', b'short', '不足 40'),
    ('legacy.txt', b'\xff' * 100, 'UTF-8'),
    ('large.txt', b'x' * (parser.MAX_FILE_BYTES+1), '超过 1 MiB'),
    ('long.txt', b'x' * (parser.MAX_CONTENT_CHARS+1), '120,000'),
])
def test_bad_files_have_actionable_bounded_errors(name,data,expected):
    with pytest.raises(parser.DocumentParseError, match=expected):
        parser.parse_document(name, encoded(data))


def test_zip_expansion_and_xml_entities_are_rejected():
    with pytest.raises(parser.DocumentParseError, match='压缩比例'):
        parser.parse_document('bomb.docx', encoded(docx_bytes(extra=b'A'*600000)))
    raw = b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><x>&leak;</x>'
    with pytest.raises(parser.DocumentParseError, match='XML 声明'):
        parser.parse_document('entity.docx', encoded(docx_bytes(xml=raw)))


def test_utf8_markdown_preserves_original_text():
    result = parser.parse_document('笔记.md', encoded(('\ufeff# 资料\n\n'+CONTENT).encode()))
    assert result['format'] == 'markdown' and result['content'].startswith('# 资料')


def test_invalid_base64_and_timeout_do_not_expose_parser_details(monkeypatch):
    with pytest.raises(parser.DocumentParseError, match='传输内容无效'):
        parser.parse_document('guide.pdf', 'not-base64!!!!')
    def timed_out(*args,**kwargs):
        raise subprocess.TimeoutExpired('private input should not be returned', 12)
    monkeypatch.setattr(parser.subprocess, 'run', timed_out)
    with pytest.raises(parser.DocumentParseError, match='已停止处理') as caught:
        parser.parse_document('guide.pdf', encoded(b'%PDF-synthetic'))
    assert 'private' not in str(caught.value)


def test_preview_does_not_index_and_confirmation_keeps_scope_quota(teams):
    from app.saas.commerce import provision_plan
    owner, other = teams
    provision_plan(owner.org, code='team', limits={'ai_requests':0,'documents':1,'members':3})
    base = owner.client.post('/api/v04/knowledge-bases', json={'name':'导入专用合成库'}).json()
    preview = owner.client.post('/api/knowledge/import-preview', json={'filename':'产品资料.docx','content_base64':encoded(docx_bytes()),'knowledge_base_id':base['id']})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result['indexed'] is False and result['knowledge_base_id'] == base['id']
    params = {'knowledge_base_id':base['id']}
    assert owner.client.get('/api/knowledge/documents', params=params).json()['total'] == 0
    assert owner.client.get('/api/saas/billing').json()['usage']['ai_requests']['used'] == 0
    saved = owner.client.post('/api/knowledge/documents', json={key:result[key] for key in ['title','content','format','knowledge_base_id']})
    assert saved.status_code == 201, saved.text
    assert owner.client.get('/api/knowledge/documents', params=params).json()['total'] == 1
    assert other.client.get('/api/knowledge/documents').json()['total'] == 0
    denied = owner.client.post('/api/knowledge/documents', json={'title':'Second','content':'第二份不同正文。'+CONTENT,**params})
    assert denied.status_code == 429, denied.text
    assert owner.client.get('/api/knowledge/documents', params=params).json()['total'] == 1


def test_preview_respects_csrf_role_and_connection_gate(application, teams):
    owner,_ = teams
    payload = {'filename':'note.txt','content_base64':encoded(CONTENT.encode())}
    # Middleware checks happen before parser work; no preview is returned.
    assert application.anonymous.post('/api/knowledge/import-preview',json=payload).status_code in {401,403}
    assert owner.client.post('/api/knowledge/import-preview',json=payload,headers={'X-CSRF-Token':''}).status_code == 403
    viewer = invited_member(application, owner, 'viewer')
    assert viewer.client.post('/api/knowledge/import-preview',json=payload).status_code == 403
    assert owner.client.patch('/api/saas/connections/markdown',json={'enabled':False}).status_code == 200
    denied = owner.client.post('/api/knowledge/import-preview',json=payload)
    assert denied.status_code == 422 and '尚未启用' in denied.text


def test_preview_wrong_library_and_oversize_requests_do_not_import(teams):
    owner,_ = teams
    payload = {'filename':'note.txt','content_base64':encoded(CONTENT.encode()),'knowledge_base_id':98765}
    result = owner.client.post('/api/knowledge/import-preview',json=payload)
    assert result.status_code == 422, result.text
    assert owner.client.post('/api/knowledge/import-preview',content=b' '*(2*1024*1024+1)).status_code == 413
    assert owner.client.get('/api/knowledge/documents').json()['total'] == 0
