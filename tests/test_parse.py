import io
import zipfile

import pytest

from parse import ParseError, to_text


def test_csv():
    text = to_text("sizing.csv", b"Component,Spec\nWeb,t4g.nano\n")
    assert "Web | t4g.nano" in text


def test_docx_paragraphs_and_tables():
    xml = ('<w:document><w:body><w:p><w:r><w:t>Region: Mumbai</w:t></w:r></w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Web</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>m6i.large</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    text = to_text("design.docx", buf.getvalue())
    assert "Region: Mumbai" in text and "Web" in text and "m6i.large" in text


def test_xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["Component", "Type"])
    wb.active.append(["DB", "db.r6g.large"])
    buf = io.BytesIO()
    wb.save(buf)
    assert "DB | db.r6g.large" in to_text("sizing.xlsx", buf.getvalue())


def test_unsupported_and_empty_files():
    with pytest.raises(ParseError):
        to_text("image.png", b"\x89PNG")
    with pytest.raises(ParseError):
        to_text("empty.txt", b"   ")
