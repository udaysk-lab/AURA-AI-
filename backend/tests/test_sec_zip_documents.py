"""A zip-based document must be the family its name says, and neither family may hold macros or scripts."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.device import filetypes
from tests.test_r2b_open_files import make, w  # noqa: F401

ODF_MIME = "application/vnd.oasis.opendocument.text"


def zipped(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


OOXML_OK = [("[Content_Types].xml", "<Types/>"), ("word/document.xml", "x")]
ODF_OK = [("mimetype", ODF_MIME), ("content.xml", "x")]


@pytest.mark.parametrize("name,members,kind", [
    ("a.docx", OOXML_OK, "document"),
    ("a.xlsx", OOXML_OK, "document"),
    ("a.pptx", OOXML_OK, "document"),
    ("a.odt", ODF_OK, "document"),
    ("a.ods", ODF_OK, "document"),
    ("a.odp", ODF_OK, "document"),
    ("a.odg", ODF_OK, "document"),
    # the family of the content must match the name
    ("a.docx", ODF_OK, "unchecked"),
    ("a.xlsx", ODF_OK, "unchecked"),
    ("a.pptx", ODF_OK + [("[Content_Types].xml", "<Types/>")], "unchecked"),
    ("a.docx", OOXML_OK + [("mimetype", ODF_MIME)], "unchecked"),
    ("a.odt", OOXML_OK, "unchecked"),
    ("a.odt", ODF_OK + [("[Content_Types].xml", "<Types/>")], "unchecked"),
    ("a.odt", [("mimetype", "text/plain"), ("content.xml", "x")], "unchecked"),
    ("a.odt", [("mimetype", "application/vnd.oasis.opendocument"), ("content.xml", "x")], "unchecked"),
    ("a.odt", [("content.xml", "x")], "unchecked"),
    ("a.docx", [("word/document.xml", "x")], "unchecked"),
    # macros and scripts, in either family and under either kind of name
    ("a.docx", OOXML_OK + [("word/vbaProject.bin", "x")], "unchecked"),
    ("a.xlsx", OOXML_OK + [("xl/vbaProject.bin", "x")], "unchecked"),
    ("a.docx", OOXML_OK + [("Basic/Standard/Module1.xml", "x")], "unchecked"),
    ("a.docx", OOXML_OK + [("Scripts/python/x.py", "x")], "unchecked"),
    ("a.pptx", OOXML_OK + [("macros/m.xml", "x")], "unchecked"),
    ("a.odt", ODF_OK + [("Basic/Standard/Module1.xml", "x")], "unchecked"),
    ("a.odt", ODF_OK + [("Scripts/python/x.py", "x")], "unchecked"),
    ("a.odt", ODF_OK + [("macros/m.xml", "x")], "unchecked"),
    ("a.odt", ODF_OK + [("word/vbaProject.bin", "x")], "unchecked"),
    ("a.ods", ODF_OK + [("Object 1/VBAPROJECT.BIN", "x")], "unchecked"),
])
def test_a_zip_document_is_judged_by_what_it_is(w, name, members, kind):
    path = make(w, name, zipped(members))
    assert filetypes.classify(path).kind == kind


def test_a_zip_with_too_many_parts_is_not_checked(w):
    path = make(w, "big.docx", zipped(OOXML_OK + [(f"p/{i}.xml", "") for i in range(5001)]))
    assert filetypes.classify(path).kind == "unchecked"


@pytest.mark.parametrize("name", ["report.docx", "sheet.xlsx", "deck.pptx"])
def test_an_opendocument_file_with_macros_under_an_office_name_asks(w, name):
    data = zipped(ODF_OK + [("Basic/Standard/Module1.xml", "Sub Main\n Shell(\"calc\")\nEnd Sub"),
                            ("Scripts/python/x.py", "import os\n")])
    assert filetypes.classify(make(w, "control.odt", data)).kind == "unchecked"
    path = make(w, name, data)
    assert filetypes.classify(path).kind == "unchecked"
    from tests.sec_world import run

    d, out = run(w, "open_app", {"name": path})
    assert (d.kind, d.reason_code, d.always_user) == ("ask", "UNCHECKED_OPEN", True)
    assert w.opened == []


def test_an_opendocument_file_with_no_macros_under_an_office_name_asks_too(w):
    path = make(w, "plain.docx", zipped(ODF_OK))
    assert filetypes.classify(path).kind == "unchecked"
