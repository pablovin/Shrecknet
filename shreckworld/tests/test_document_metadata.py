from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZipFile

import fitz
import pytest
from PyPDF2 import PdfWriter
from PyPDF2.generic import DecodedStreamObject, NameObject

from app.services.document_metadata import extract_document_metadata, resolve_upload_metadata


def pdf_bytes(metadata=None):
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    if metadata:
        writer.add_metadata(metadata)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def docx_bytes(title="A book", creator="An author", description="A description"):
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("docProps/core.xml", f'''<cp:coreProperties
            xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
            xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:title>{escape(title)}</dc:title><dc:creator>{escape(creator)}</dc:creator>
            <dc:description>{escape(description)}</dc:description></cp:coreProperties>''')
    return stream.getvalue()


def test_pdf_metadata_and_bounded_cover():
    result = extract_document_metadata(pdf_bytes({"/Title": " Book ", "/Author": "Writer", "/Subject": "Summary"}), "book.pdf")
    assert (result.title, result.authors, result.description) == ("Book", "Writer", "Summary")
    pixmap = fitz.Pixmap(result.cover)
    assert max(pixmap.width, pixmap.height) <= 1200


def test_xmp_metadata():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    xmp = DecodedStreamObject()
    xmp.set_data(b'''<x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title><rdf:Alt><rdf:li xml:lang="x-default">XMP title</rdf:li></rdf:Alt></dc:title>
          <dc:creator><rdf:Seq><rdf:li>A</rdf:li><rdf:li>B</rdf:li></rdf:Seq></dc:creator>
          <dc:description><rdf:Alt><rdf:li xml:lang="x-default">XMP summary</rdf:li></rdf:Alt></dc:description>
        </rdf:Description>
      </rdf:RDF></x:xmpmeta>''')
    xmp[NameObject("/Type")] = NameObject("/Metadata")
    xmp[NameObject("/Subtype")] = NameObject("/XML")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    stream = BytesIO()
    writer.write(stream)
    result = extract_document_metadata(stream.getvalue(), "book.pdf")
    assert (result.title, result.authors, result.description) == ("XMP title", "A, B", "XMP summary")


def test_docx_properties():
    result = extract_document_metadata(docx_bytes(), "book.docx")
    assert (result.title, result.authors, result.description, result.cover) == ("A book", "An author", "A description", None)


@pytest.mark.parametrize("filename,raw", [
    ("broken.pdf", b"invalid"), ("broken.docx", b"invalid"),
    ("notes.md", b"# Notes"), ("notes.txt", b"Notes"),
    ("blank.pdf", pdf_bytes()),
])
def test_fallback(filename, raw):
    result = resolve_upload_metadata(raw, filename=filename, title=None, authors=None, description=None, auto_extract_metadata=True)
    assert result.title == filename.rsplit(".", 1)[0]
    assert result.authors is None
    assert result.description is None


def test_manual_fields_win_and_disabled_does_not_parse(monkeypatch):
    raw = docx_bytes()
    result = resolve_upload_metadata(raw, filename="book.docx", title="Manual", authors="Editor", description="Custom", auto_extract_metadata=True)
    assert (result.title, result.authors, result.description) == ("Manual", "Editor", "Custom")
    def unexpected(*args):
        pytest.fail("Autofill is disabled")
    monkeypatch.setattr("app.services.document_metadata.extract_document_metadata", unexpected)
    assert resolve_upload_metadata(raw, filename="book.docx", title="Manual", authors=None, description=None, auto_extract_metadata=False).authors is None
    with pytest.raises(ValueError, match="title is required"):
        resolve_upload_metadata(raw, filename="book.docx", title=None, authors=None, description=None, auto_extract_metadata=False)


def test_extracted_values_respect_column_limits():
    result = resolve_upload_metadata(docx_bytes("t" * 300, "a" * 600), filename="book.docx", title=None, authors=None, description=None, auto_extract_metadata=True)
    assert len(result.title) == 255
    assert len(result.authors) == 512


def test_encrypted_pdf_falls_back():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    stream = BytesIO()
    writer.write(stream)
    result = extract_document_metadata(stream.getvalue(), "locked.pdf")
    assert result.title is None
    assert result.cover is None


def test_cover_failure_preserves_metadata(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("render unavailable")
    monkeypatch.setattr("app.services.document_metadata.fitz.open", fail)
    result = extract_document_metadata(pdf_bytes({"/Title": "Book"}), "book.pdf")
    assert result.title == "Book"
    assert result.cover is None


def test_docx_missing_or_oversized_properties():
    for properties in (None, b"x" * (1024 * 1024 + 1)):
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            if properties is not None:
                archive.writestr("docProps/core.xml", properties)
        result = extract_document_metadata(stream.getvalue(), "book.docx")
        assert result.title is None
        assert result.authors is None


def test_partial_manual_metadata_still_fills_empty_fields():
    result = resolve_upload_metadata(docx_bytes(), filename="book.docx", title="Manual", authors=" ", description=None, auto_extract_metadata=True)
    assert (result.title, result.authors, result.description) == ("Manual", "An author", "A description")
