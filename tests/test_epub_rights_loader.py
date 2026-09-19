"""The loader's side of the rights work: refuse DRM, and keep the rights
metadata the source carried.
"""

import re
import zipfile

import pytest
from ebooklib import epub

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import package_prefixes
from book_maker.loader.rights import DRM_MESSAGE

RIGHTS_METADATA = (
    "<dc:rights>Copyright 2020 Someone</dc:rights>"
    '<meta property="dcterms:rightsHolder">Someone Publishing</meta>'
    '<meta property="dcterms:license">https://example.org/license</meta>'
    '<meta property="tdm:reservation">1</meta>'
    '<meta property="tdm:policy">https://example.org/policy.json</meta>'
)


class NeverBuiltModel:
    """A translator that records that it was constructed at all."""

    instances = []

    def __init__(self, *args, **kwargs):
        type(self).instances.append(self)


def _write_epub(path, identifier="urn:uuid:source-id-123"):
    book = epub.EpubBook()
    if identifier:
        book.set_identifier(identifier)
    book.set_title("Rights fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>Body text</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)
    return path


def _opf_member(path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return name, archive.read(name).decode("utf-8")


def _rewrite_member(path, member, content):
    replacement = path.with_suffix(".replacement.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    content.encode("utf-8")
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)


def _set_package_prefix(opf, prefix):
    """Replace the package's `prefix` attribute, or drop it when None.

    ebooklib writes one of its own, so the fixtures rewrite that attribute
    rather than adding a second one (which is not well-formed XML).
    """
    if prefix is None:
        return re.sub(r'\sprefix="[^"]*"', "", opf, count=1)
    if re.search(r'<package[^>]*\sprefix="', opf):
        return re.sub(
            r'(<package[^>]*\sprefix=")[^"]*"', r"\1" + prefix + '"', opf, count=1
        )
    return opf.replace("<package ", f'<package prefix="{prefix}" ', 1)


def _with_rights_metadata(path, prefix="tdm: http://www.w3.org/ns/tdmrep#"):
    member, opf = _opf_member(path)
    opf = _set_package_prefix(opf, prefix)
    opf = re.sub(r"(<metadata[^>]*>)", r"\1" + RIGHTS_METADATA, opf, count=1)
    _rewrite_member(path, member, opf)
    return path


def _rebuilder(source, language="zh-hans", single=False, epub_name=None):
    """A loader with only what `_make_new_book` touches."""
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = source
    loader.language = language
    loader.single_translate = single
    if epub_name is not None:
        loader.epub_name = str(epub_name)
    return loader


def _rebuilt_opf(tmp_path, source, name="rebuilt.epub", epub_name=None):
    rebuilt = _rebuilder(source, epub_name=epub_name)._make_new_book(source)
    out = tmp_path / name
    epub.write_epub(str(out), rebuilt)
    return _opf_member(out)[1]


def _package_prefix(opf):
    match = re.search(r'<package[^>]*\sprefix="([^"]*)"', opf)
    return match.group(1) if match else ""


# --------------------------------------------------------------- the refusal


def test_a_drm_epub_is_refused_before_anything_else_happens(tmp_path, capsys):
    source = _write_epub(tmp_path / "protected.epub")
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("META-INF/rights.xml", "<rights/>")
    NeverBuiltModel.instances.clear()

    with pytest.raises(SystemExit) as exit_info:
        EPUBBookLoader(
            str(source),
            NeverBuiltModel,
            key="",
            resume=False,
            language="zh-hans",
        )

    assert exit_info.value.code == 1
    assert DRM_MESSAGE in capsys.readouterr().err
    # nothing was built and nothing was written before the refusal
    assert NeverBuiltModel.instances == []
    assert not list(tmp_path.glob("*.temp.bin"))
    assert not list(tmp_path.glob(".*.temp.bin"))


def test_a_clean_epub_is_not_refused(tmp_path):
    source = _write_epub(tmp_path / "clean.epub")
    loader = EPUBBookLoader(
        str(source),
        NeverBuiltModel,
        key="",
        resume=False,
        language="zh-hans",
    )
    assert loader.origin_book is not None


# ------------------------------------------------------- metadata round trip


def test_rights_metadata_survives_the_output_book(tmp_path):
    source_path = _with_rights_metadata(_write_epub(tmp_path / "rights.epub"))
    source = epub.read_epub(str(source_path))

    opf = _rebuilt_opf(tmp_path, source)

    assert "<dc:rights>Copyright 2020 Someone</dc:rights>" in opf
    assert '<meta property="dcterms:rightsHolder">Someone Publishing</meta>' in opf
    assert '<meta property="dcterms:license">https://example.org/license</meta>' in opf
    assert '<meta property="tdm:reservation">1</meta>' in opf
    assert '<meta property="tdm:policy">https://example.org/policy.json</meta>' in opf


def test_the_translation_names_its_source(tmp_path):
    source_path = _with_rights_metadata(_write_epub(tmp_path / "rights.epub"))
    source = epub.read_epub(str(source_path))

    opf = _rebuilt_opf(tmp_path, source)

    assert "<dc:source>urn:uuid:source-id-123</dc:source>" in opf


def test_the_source_is_named_once(tmp_path):
    """A book rebuilt from an earlier output already names the source; the
    same value must not be added twice (--retranslate)."""
    source_path = _write_epub(tmp_path / "sourced.epub")
    member, opf = _opf_member(source_path)
    opf = re.sub(
        r"(<metadata[^>]*>)",
        r"\1<dc:source>urn:uuid:source-id-123</dc:source>",
        opf,
        count=1,
    )
    _rewrite_member(source_path, member, opf)
    source = epub.read_epub(str(source_path))

    rebuilt = _rebuilt_opf(tmp_path, source)

    assert rebuilt.count("<dc:source>urn:uuid:source-id-123</dc:source>") == 1


def test_an_identifierless_source_is_not_named(tmp_path):
    """Nothing to point at, so nothing is invented — the same condition
    under which no translation identity is derived either."""
    source_path = _write_epub(tmp_path / "bare.epub")
    source = epub.read_epub(str(source_path))
    source.uid = None

    assert "<dc:source>" not in _rebuilt_opf(tmp_path, source)


# ------------------------------------------------- the prefix declarations


def test_the_source_prefix_declarations_are_carried(tmp_path):
    """`tdm:reservation` names a vocabulary; without the declaration the
    property is undefined and the meta says nothing (epubcheck OPF-028)."""
    source_path = _with_rights_metadata(_write_epub(tmp_path / "rights.epub"))
    source = epub.read_epub(str(source_path))

    opf = _rebuilt_opf(tmp_path, source, epub_name=source_path)
    prefix = _package_prefix(opf)

    assert "tdm: http://www.w3.org/ns/tdmrep#" in prefix
    assert prefix.count("rendition:") == 1


def test_a_source_that_declares_rendition_does_not_declare_it_twice(tmp_path):
    """ebooklib always writes the rendition mapping itself; copying the
    source's copy would declare one prefix twice in the same attribute."""
    source_path = _with_rights_metadata(
        _write_epub(tmp_path / "rendition.epub"),
        prefix=(
            "rendition: http://www.idpf.org/vocab/rendition/# "
            "tdm: http://www.w3.org/ns/tdmrep#"
        ),
    )
    source = epub.read_epub(str(source_path))

    prefix = _package_prefix(_rebuilt_opf(tmp_path, source, epub_name=source_path))

    assert prefix.count("rendition:") == 1
    assert "tdm: http://www.w3.org/ns/tdmrep#" in prefix


def test_a_source_with_no_prefix_attribute_adds_none(tmp_path):
    source_path = _write_epub(tmp_path / "plain.epub")
    member, opf = _opf_member(source_path)
    _rewrite_member(source_path, member, _set_package_prefix(opf, None))
    source = epub.read_epub(str(source_path))

    prefix = _package_prefix(_rebuilt_opf(tmp_path, source, epub_name=source_path))

    assert prefix == "rendition: http://www.idpf.org/vocab/rendition/#"


def test_a_loader_with_no_source_path_still_rebuilds(tmp_path):
    """`_make_new_book` is called on books that never came from a file."""
    source_path = _with_rights_metadata(_write_epub(tmp_path / "rights.epub"))
    source = epub.read_epub(str(source_path))

    assert _package_prefix(_rebuilt_opf(tmp_path, source))


# ----------------------------------------------------- the prefix parser


def test_package_prefixes_reads_the_pairs(tmp_path):
    source_path = _with_rights_metadata(_write_epub(tmp_path / "rights.epub"))
    assert package_prefixes(str(source_path)) == {"tdm": "http://www.w3.org/ns/tdmrep#"}


def test_package_prefixes_of_a_book_without_the_attribute(tmp_path):
    source_path = _write_epub(tmp_path / "plain.epub")
    member, opf = _opf_member(source_path)
    _rewrite_member(source_path, member, _set_package_prefix(opf, None))
    assert package_prefixes(str(source_path)) == {}


def test_package_prefixes_tolerates_a_file_it_cannot_read(tmp_path):
    path = tmp_path / "not-a-zip.epub"
    path.write_text("plain text")
    assert package_prefixes(str(path)) == {}
    assert package_prefixes(str(tmp_path / "absent.epub")) == {}


def test_package_prefixes_tolerates_an_unparsable_package(tmp_path):
    source_path = _write_epub(tmp_path / "broken.epub")
    member, _ = _opf_member(source_path)
    _rewrite_member(source_path, member, "<package")
    assert package_prefixes(str(source_path)) == {}


def test_package_prefixes_ignores_an_odd_declaration(tmp_path):
    """A trailing name with no URI is not a mapping; the pairs before it are."""
    source_path = _write_epub(tmp_path / "odd.epub")
    member, opf = _opf_member(source_path)
    _rewrite_member(
        source_path,
        member,
        _set_package_prefix(opf, "tdm: http://www.w3.org/ns/tdmrep# dangling:"),
    )
    assert package_prefixes(str(source_path)) == {"tdm": "http://www.w3.org/ns/tdmrep#"}
