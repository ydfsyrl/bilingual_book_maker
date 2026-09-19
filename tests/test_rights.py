"""The DRM check: what counts as protection, and what merely looks like it.

Every fixture here is synthetic — a zip built in `tmp_path` with exactly the
`META-INF/` members the case is about. No real book is needed, and none may
be added to `test_books/`.
"""

import zipfile

import pytest

from book_maker.loader.rights import DRM_MESSAGE, check_epub

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile media-type="application/oebps-package+xml" full-path="OEBPS/content.opf"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:uuid:synthetic</dc:identifier>
    <dc:title>Synthetic</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest/>
  <spine/>
</package>
"""


def _encryption(*algorithms):
    bodies = "".join(
        f'<enc:EncryptedData><enc:EncryptionMethod Algorithm="{algorithm}"/>'
        f'<enc:CipherData><enc:CipherReference URI="OEBPS/ch{index}.xhtml"/>'
        f"</enc:CipherData></enc:EncryptedData>"
        for index, algorithm in enumerate(algorithms)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
        ' xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        f"{bodies}</encryption>"
    )


def _epub(tmp_path, name="book.epub", members=None):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", OPF)
        for member, content in (members or {}).items():
            archive.writestr(member, content)
    return path


def test_an_aes_algorithm_is_drm(tmp_path):
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _encryption(
                "http://www.w3.org/2001/04/xmlenc#aes256-cbc"
            )
        },
    )
    assert check_epub(str(path)) == "drm"


def test_font_obfuscation_alone_is_not_drm(tmp_path):
    """The IDPF algorithm scrambles an embedded font, not the content."""
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _encryption("http://www.idpf.org/2008/embedding")
        },
    )
    assert check_epub(str(path)) == "ok"


def test_the_adobe_font_algorithm_is_not_drm(tmp_path):
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _encryption("http://ns.adobe.com/pdf/enc#RC")
        },
    )
    assert check_epub(str(path)) == "ok"


def test_one_real_algorithm_among_font_ones_is_drm(tmp_path):
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _encryption(
                "http://www.idpf.org/2008/embedding",
                "http://www.w3.org/2001/04/xmlenc#aes128-cbc",
            )
        },
    )
    assert check_epub(str(path)) == "drm"


@pytest.mark.parametrize(
    "member", ["META-INF/rights.xml", "META-INF/license.lcpl", "META-INF/sinf.xml"]
)
def test_a_vendor_rights_file_is_drm(tmp_path, member):
    path = _epub(tmp_path, members={member: "<rights/>"})
    assert check_epub(str(path)) == "drm"


def test_a_signature_is_not_protection(tmp_path):
    """`signatures.xml` asserts integrity; translating breaks the signature,
    which is fine and is not circumvention."""
    path = _epub(tmp_path, members={"META-INF/signatures.xml": "<signatures/>"})
    assert check_epub(str(path)) == "ok"


def test_a_plain_epub_is_ok(tmp_path):
    assert check_epub(str(_epub(tmp_path))) == "ok"


def test_an_unparsable_encryption_declaration_fails_closed(tmp_path):
    """A declaration that cannot be read cannot be read as harmless."""
    path = _epub(tmp_path, members={"META-INF/encryption.xml": "<encryption"})
    assert check_epub(str(path)) == "drm"


def test_a_file_that_is_not_a_zip_is_left_to_the_loader(tmp_path):
    """Not this function's error to report — the reader gives a better one."""
    path = tmp_path / "not-an-epub.epub"
    path.write_text("this is not a zip")
    assert check_epub(str(path)) == "ok"


def test_a_missing_file_is_left_to_the_loader(tmp_path):
    assert check_epub(str(tmp_path / "absent.epub")) == "ok"


def test_the_refusal_message_offers_no_way_around_it():
    assert DRM_MESSAGE
    assert "\n" not in DRM_MESSAGE
    lowered = DRM_MESSAGE.lower()
    assert "drm" in lowered
    for forbidden in ("dedrm", "calibre", "strip", "first", "instead"):
        assert forbidden not in lowered


# ------------------------------------------------- finding 1: fail closed


def _raw_encryption(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
        ' xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        f"{body}</encryption>"
    )


def test_an_entry_with_no_encryption_method_is_drm(tmp_path):
    """Finding 1: an EncryptedData that names no algorithm was being skipped
    and the book passed. Nothing about it says the content is in the clear,
    so it has to be read as protection."""
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _raw_encryption(
                "<enc:EncryptedData><enc:CipherData>"
                '<enc:CipherReference URI="OEBPS/ch0.xhtml"/>'
                "</enc:CipherData></enc:EncryptedData>"
            )
        },
    )
    assert check_epub(str(path)) == "drm"


def test_a_method_in_an_unexpected_namespace_is_drm(tmp_path):
    """Finding 1: the check matched EncryptionMethod in the xmlenc namespace
    only, so a producer using another one slipped an AES declaration past it."""
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _raw_encryption(
                '<enc:EncryptedData><EncryptionMethod xmlns="http://example.invalid/enc"'
                ' Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/>'
                "</enc:EncryptedData>"
            )
        },
    )
    assert check_epub(str(path)) == "drm"


def test_an_encrypted_data_in_an_unexpected_namespace_still_counts(tmp_path):
    """Finding 1: EncryptedData is matched by local name, any namespace."""
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _raw_encryption(
                '<EncryptedData xmlns="http://example.invalid/enc">'
                '<EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>'
                "</EncryptedData>"
            )
        },
    )
    assert check_epub(str(path)) == "drm"


def test_a_font_algorithm_in_an_unexpected_namespace_is_still_a_font(tmp_path):
    """Finding 1: matching by local name must not turn the font case into a
    refusal — the algorithm URI is what decides, not the element's namespace."""
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _raw_encryption(
                '<EncryptedData xmlns="http://example.invalid/enc">'
                '<EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
                "</EncryptedData>"
            )
        },
    )
    assert check_epub(str(path)) == "ok"


def test_a_declaration_that_declares_nothing_is_drm(tmp_path):
    """Finding 1: an encryption.xml holding no EncryptedData is malformed,
    and malformed fails closed like an unparsable one."""
    path = _epub(tmp_path, members={"META-INF/encryption.xml": _raw_encryption("")})
    assert check_epub(str(path)) == "drm"


def test_a_decoy_method_below_cipher_data_does_not_clear_the_file(tmp_path):
    """Finding 1 (re-review): the search walked every descendant, so a font
    algorithm parked anywhere inside the entry — including under CipherData,
    where an EncryptionMethod has no meaning — cleared it. Only a direct
    child of EncryptedData declares how that resource is encrypted."""
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _raw_encryption(
                "<enc:EncryptedData><enc:CipherData>"
                '<enc:CipherReference URI="OEBPS/ch0.xhtml"/>'
                '<enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
                "</enc:CipherData></enc:EncryptedData>"
            )
        },
    )
    assert check_epub(str(path)) == "drm"


def test_a_direct_child_method_still_clears_a_font(tmp_path):
    """Finding 1 (re-review): tightening to direct children must not turn the
    ordinary font declaration into a refusal."""
    path = _epub(
        tmp_path,
        members={
            "META-INF/encryption.xml": _raw_encryption(
                "<enc:EncryptedData>"
                '<enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
                "<enc:CipherData>"
                '<enc:CipherReference URI="OEBPS/fonts/x.otf"/>'
                "</enc:CipherData></enc:EncryptedData>"
            )
        },
    )
    assert check_epub(str(path)) == "ok"
