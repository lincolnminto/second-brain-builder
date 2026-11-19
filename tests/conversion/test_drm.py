"""DRM detection: protected Sources are refused before any conversion work."""

import hashlib
import struct
from pathlib import Path

import pytest
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives.ciphers import Cipher

from second_brain_builder.conversion import (
    ConversionError,
    ConversionOptions,
    DrmProtectedError,
    convert_source,
)
from second_brain_builder.conversion.drm import check_drm
from second_brain_builder.conversion.formats import source_format


def _restricted_pdf(path: Path, permissions: int) -> Path:
    """Synthetic Standard revision 3 PDF with an empty user password and real encryption."""
    padding = bytes.fromhex("28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a")
    file_id = bytes.fromhex("ab" * 16)

    def md5(data: bytes) -> bytes:
        return hashlib.md5(data, usedforsecurity=False).digest()

    def rc4(key: bytes, data: bytes) -> bytes:
        encryptor = Cipher(ARC4(key), mode=None).encryptor()
        return encryptor.update(data) + encryptor.finalize()

    def rounds(key: bytes, data: bytes) -> bytes:
        for index in range(20):
            data = rc4(bytes(byte ^ index for byte in key), data)
        return data

    owner_key = md5((b"synthetic-owner" + padding)[:32])
    for _ in range(50):
        owner_key = md5(owner_key)
    owner = rounds(owner_key, padding)
    key = md5(padding + owner + struct.pack("<i", permissions) + file_id)
    for _ in range(50):
        key = md5(key)
    user = rounds(key, md5(padding + file_id)) + bytes(16)
    text = b"BT /F1 12 Tf 72 720 Td (Synthetic restricted text.) Tj ET"
    stream_key = md5(key + (5).to_bytes(3, "little") + bytes(2))[:16]
    stream = rc4(stream_key, text)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Filter /Standard /V 2 /R 3 /Length 128 /P %d /O <%s> /U <%s> >>"
        % (permissions, owner.hex().encode(), user.hex().encode()),
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(content))
        content += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    startxref = len(content)
    content += b"xref\n0 7\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        content += b"%010d 00000 n \n" % offset
    content += (
        b"trailer\n<< /Size 7 /Root 1 0 R /Encrypt 6 0 R /ID [<%s> <%s>] >>\n"
        b"startxref\n%d\n%%%%EOF\n" % (file_id.hex().encode(), file_id.hex().encode(), startxref)
    )
    path.write_bytes(content)
    return path


@pytest.mark.parametrize("forbidden_bit", [4, 9])
def test_pdf_that_opens_but_forbids_copying_or_extraction_is_refused(tmp_path, forbidden_bit):
    pdfium = pytest.importorskip("pypdfium2")
    path = _restricted_pdf(tmp_path / "restricted.pdf", -4 & ~(1 << forbidden_bit))
    # A successful open and extraction do not prove that extraction is allowed.
    with pdfium.PdfDocument(path) as document:
        page = document[0]
        textpage = page.get_textpage()
        assert "Synthetic restricted text." in textpage.get_text_range()
        textpage.close()
        page.close()
    with pytest.raises(DrmProtectedError, match="permissions"):
        check_drm(path, source_format("application/pdf"))


def test_pdf_with_copy_and_extraction_permissions_is_allowed(tmp_path):
    pytest.importorskip("pypdfium2")
    path = _restricted_pdf(tmp_path / "allowed.pdf", -4)
    check_drm(path, source_format("application/pdf"))


AES_ENCRYPTION_XML = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
    <enc:CipherData><enc:CipherReference URI="OEBPS/chapter1.xhtml"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""
# The same content with the XML Encryption namespace as the default namespace.
AES_DEFAULT_NAMESPACE_XML = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">
    <EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/>
    <CipherData><CipherReference URI="OEBPS/chapter1.xhtml"/></CipherData>
  </EncryptedData>
</encryption>
"""
FONT_OBFUSCATION_XML = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
    <enc:CipherData><enc:CipherReference URI="OEBPS/fonts/serif.otf"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""


@pytest.fixture
def options(tmp_path):
    return ConversionOptions(models_dir=tmp_path / "models")


@pytest.mark.parametrize(
    "member", ["META-INF/rights.xml", "META-INF/license.lcpl", "META-INF/sinf.xml"]
)
def test_epub_with_a_drm_license_file_is_refused(synthetic, tmp_path, options, member):
    epub = synthetic.epub(
        "locked.epub",
        title="Locked Tides",
        author="Ada Example",
        chapters=[("One", "Protected text.")],
        extra_members={member: "<rights/>"},
    )
    assets_dir = tmp_path / "assets"

    with pytest.raises(DrmProtectedError, match=member):
        convert_source(epub, assets_dir, options)
    assert not assets_dir.exists()


@pytest.mark.parametrize(
    ("encryption_xml", "algorithm"),
    [(AES_ENCRYPTION_XML, "aes128-cbc"), (AES_DEFAULT_NAMESPACE_XML, "aes256-cbc")],
    ids=["prefixed", "default-namespace"],
)
def test_epub_with_encrypted_content_is_refused(
    synthetic, tmp_path, options, encryption_xml, algorithm
):
    epub = synthetic.epub(
        "encrypted.epub",
        title="Encrypted Tides",
        author="Ada Example",
        chapters=[("One", "Protected text.")],
        extra_members={"META-INF/encryption.xml": encryption_xml},
    )

    with pytest.raises(DrmProtectedError, match=algorithm):
        convert_source(epub, tmp_path / "assets", options)


@pytest.mark.parametrize("member", ["mimetype", "META-INF/encryption.xml", "OEBPS/chapter1.xhtml"])
def test_password_protected_epub_is_refused(synthetic, tmp_path, options, member):
    epub = synthetic.epub(
        "password.epub",
        title="Locked Tides",
        author="Ada Example",
        chapters=[("One", "Protected text.")],
        extra_members={"META-INF/encryption.xml": FONT_OBFUSCATION_XML},
    )
    synthetic.flag_zip_member_encrypted(epub, member)
    assets_dir = tmp_path / "assets"

    with pytest.raises(DrmProtectedError, match="password"):
        convert_source(epub, assets_dir, options)
    assert not assets_dir.exists()


@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    "encryption_xml",
    [
        # Unclosed tags with no ">": a backtracking pattern takes minutes on this.
        "<EncryptionMethod " * 50_000,
        # Entity declarations can expand without limit, so a DTD is refused.
        '<?xml version="1.0"?><!DOCTYPE encryption [<!ENTITY a "aaaa">]><encryption>&a;'
        "</encryption>",
    ],
    ids=["unclosed-tags", "doctype"],
)
def test_hostile_encryption_xml_is_rejected_quickly(synthetic, tmp_path, options, encryption_xml):
    epub = synthetic.epub(
        "hostile.epub",
        title="Hostile Tides",
        author="Ada Example",
        chapters=[("One", "Text.")],
        extra_members={"META-INF/encryption.xml": encryption_xml},
    )
    assets_dir = tmp_path / "assets"

    with pytest.raises(ConversionError, match=r"encryption\.xml"):
        convert_source(epub, assets_dir, options)
    assert not assets_dir.exists()


@pytest.mark.parametrize(("name", "encryption"), [("old.mobi", 1), ("book.azw3", 2)])
def test_kindle_file_with_an_encryption_type_is_refused(
    synthetic, tmp_path, options, name, encryption
):
    ebook = synthetic.palmdb(name, encryption=encryption)
    assets_dir = tmp_path / "assets"

    with pytest.raises(DrmProtectedError, match=f"encryption type {encryption}"):
        convert_source(ebook, assets_dir, options)
    assert not assets_dir.exists()


@pytest.mark.parametrize(
    ("security_handler", "message"),
    [
        ("Standard", "needs a password"),
        # Store DRM: Adobe ADEPT and Adobe public-key (certificate) security.
        ("EBX_HANDLER", "security handler"),
        ("Adobe.PubSec", "security handler"),
    ],
)
def test_protected_pdf_is_refused(synthetic, tmp_path, options, security_handler, message):
    pytest.importorskip("pypdfium2", reason="PDF support comes with the worker dependencies")
    pdf = synthetic.pdf(
        "locked.pdf",
        ["Locked report"],
        title="Locked",
        author="Ada Example",
        security_handler=security_handler,
    )
    assets_dir = tmp_path / "assets"

    with pytest.raises(DrmProtectedError, match=message):
        convert_source(pdf, assets_dir, options)
    assert not assets_dir.exists()
