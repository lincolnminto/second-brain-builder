"""DRM detection. Conversion refuses protected Sources and never removes DRM."""

from pathlib import Path
from xml.parsers import expat

from second_brain_builder.conversion.formats import (
    ZIP_ENCRYPTED_FLAG,
    ZIP_READ_ERRORS,
    SourceFormat,
    SourceKind,
    _open_zip,
)
from second_brain_builder.conversion.models import ConversionError, DrmProtectedError

# EPUB files whose presence means a DRM scheme: Adobe ADEPT, Readium LCP, Apple FairPlay.
_EPUB_DRM_MEMBERS = ("META-INF/rights.xml", "META-INF/license.lcpl", "META-INF/sinf.xml")
_EPUB_ENCRYPTION_MEMBER = "META-INF/encryption.xml"
# Font obfuscation (IDPF and Adobe) also uses encryption.xml, but it is not DRM.
_FONT_OBFUSCATION_ALGORITHMS = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
}
# Real encryption.xml files are a few kilobytes: one entry per encrypted file.
_ENCRYPTION_XML_MAX_BYTES = 1024 * 1024
_ENCRYPTION_METHOD = "EncryptionMethod"

_PALMDB_HEADER_BYTES = 78
_PALMDB_RECORD_COUNT_OFFSET = 76
_PALMDOC_ENCRYPTION_OFFSET = 12

# PDFium load errors: FPDF_ERR_PASSWORD (a password is needed) and FPDF_ERR_SECURITY
# (an unsupported security handler, such as certificate or plug-in DRM).
_PDFIUM_PASSWORD_ERROR = 4
_PDFIUM_SECURITY_ERROR = 5
# PDF permission bits are numbered from 1 in the specification. Bit 5 allows
# copying/extraction; revision 3 and later also have bit 10 for text extraction.
_PDF_COPY_PERMISSION = 1 << 4
_PDF_EXTRACT_PERMISSION = 1 << 9


def check_drm(path: Path, fmt: SourceFormat) -> None:
    """Raise ``DrmProtectedError`` if the Source is protected."""
    if fmt.media_type == "application/epub+zip":
        _check_epub(path)
    elif fmt.kind is SourceKind.KINDLE:
        _check_mobi(path)
    elif fmt.media_type == "application/pdf":
        _check_pdf(path)


def _check_epub(path: Path) -> None:
    try:
        with _open_zip(path) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            for member in _EPUB_DRM_MEMBERS:
                if member in names:
                    raise DrmProtectedError(f"The EPUB has DRM ({member}).")
            if any(info.flag_bits & ZIP_ENCRYPTED_FLAG for info in infos):
                raise DrmProtectedError(
                    "The EPUB needs a password: files in its ZIP archive are encrypted."
                )
            if _EPUB_ENCRYPTION_MEMBER not in names:
                return
            info = archive.getinfo(_EPUB_ENCRYPTION_MEMBER)
            if info.file_size > _ENCRYPTION_XML_MAX_BYTES:
                raise DrmProtectedError("The EPUB has an unusually large encryption.xml.")
            encryption_xml = archive.read(info)
    except ZIP_READ_ERRORS as error:
        raise ConversionError(f"The EPUB is not a readable ZIP archive: {error}") from error
    algorithms = _encryption_algorithms(encryption_xml)
    drm_algorithms = sorted(algorithms - _FONT_OBFUSCATION_ALGORITHMS)
    if drm_algorithms:
        raise DrmProtectedError(f"The EPUB has encrypted content ({', '.join(drm_algorithms)}).")


def _encryption_algorithms(encryption_xml: bytes) -> set[str]:
    """The Algorithm of each EncryptionMethod element in an EPUB encryption.xml.

    Expat reads the file in linear time. A document type declaration is refused,
    so no entity can expand, and an encryption.xml that is not well-formed XML is
    an error: Conversion cannot tell if the content is encrypted.
    """
    algorithms: set[str] = set()

    def refuse_doctype(*_args: object) -> None:
        raise ConversionError("The EPUB encryption.xml has a document type declaration.")

    def start_element(name: str, attributes: dict[str, str]) -> None:
        # With namespace processing, a name is "<namespace URI> <local name>".
        algorithm = attributes.get("Algorithm", "").strip()
        if algorithm and name.rpartition(" ")[2] == _ENCRYPTION_METHOD:
            algorithms.add(algorithm)

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartDoctypeDeclHandler = refuse_doctype
    parser.StartElementHandler = start_element
    try:
        parser.Parse(encryption_xml, True)
    except expat.ExpatError as error:
        raise ConversionError(f"The EPUB encryption.xml is not valid XML: {error}") from error
    return algorithms


def _check_mobi(path: Path) -> None:
    """Read the encryption field of record 0 in a MOBI or AZW3 (PalmDB) file."""
    with path.open("rb") as file:
        header = file.read(_PALMDB_HEADER_BYTES + 8)
        if len(header) < _PALMDB_HEADER_BYTES + 8:
            raise ConversionError("The ebook is too short to be a MOBI or AZW3 file.")
        record_count = int.from_bytes(
            header[_PALMDB_RECORD_COUNT_OFFSET:_PALMDB_HEADER_BYTES], "big"
        )
        if record_count < 1:
            raise ConversionError("The ebook has no records.")
        record0_offset = int.from_bytes(
            header[_PALMDB_HEADER_BYTES : _PALMDB_HEADER_BYTES + 4], "big"
        )
        file.seek(record0_offset + _PALMDOC_ENCRYPTION_OFFSET)
        field = file.read(2)
    if len(field) < 2:
        raise ConversionError("The ebook header is truncated.")
    encryption = int.from_bytes(field, "big")
    if encryption != 0:
        raise DrmProtectedError(f"The ebook has DRM (MOBI encryption type {encryption}).")


def _check_pdf(path: Path) -> None:
    """Reject passwords, unsupported security handlers, and extraction restrictions."""
    try:
        import pypdfium2  # type: ignore[import-untyped]
    except ImportError as error:
        raise ConversionError(
            "PDF support needs the worker dependencies (pypdfium2 comes with Docling)."
        ) from error
    try:
        document = pypdfium2.PdfDocument(path)
    except pypdfium2.PdfiumError as error:
        if error.err_code == _PDFIUM_PASSWORD_ERROR:
            raise DrmProtectedError("The PDF needs a password.") from error
        if error.err_code == _PDFIUM_SECURITY_ERROR:
            raise DrmProtectedError("The PDF uses an unsupported security handler.") from error
        raise ConversionError(f"PDFium cannot open the PDF: {error}") from error
    try:
        permissions = pypdfium2.raw.FPDF_GetDocUserPermissions(document)
        revision = pypdfium2.raw.FPDF_GetSecurityHandlerRevision(document)
        if not permissions & _PDF_COPY_PERMISSION or (
            revision >= 3 and not permissions & _PDF_EXTRACT_PERMISSION
        ):
            raise DrmProtectedError("The PDF permissions forbid text extraction or copying.")
    finally:
        document.close()
