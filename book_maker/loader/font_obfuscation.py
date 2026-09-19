"""Undo EPUB font obfuscation so the translated book can ship plain fonts.

Obfuscation is not DRM and this is not circumvention: both algorithms are
published in the OCF specification, both keys are derived from the book's
own public identifier, and the scheme exists so a foundry's font is not
trivially extractable — not to control who may read the book. `check_epub`
has already refused anything else that `META-INF/encryption.xml` might
declare, so by the time this runs the only algorithms that can appear are
the two below.

Why both directions: a foundry's embedding licence typically permits
embedding only in obfuscated form. A publisher who obfuscated a font did
so to comply, and a translated edition that shipped the same font in the
clear would undo that compliance on the publisher's behalf — the output
must be no less compliant than the input it was made from. So the round
trip is the design: unscramble on read, because the text has to be worked
on and `META-INF/encryption.xml` is not a manifest item so ebooklib
carries neither it nor its meaning into the output; scramble again after
the book is written, keyed on the *output* book's own identifier, and
write the declaration back beside it. Calibre's EPUB output does the same
thing (`encrypt_fonts`).

This runs on the raw OCF zip, beside the reader rather than inside it — the
package document is read directly (`helper.read_package`) rather than
through ebooklib, because the key is derived from the identifier the
package *names*, which is not the one ebooklib reports.
"""

import hashlib
import os
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape
from urllib.parse import quote, unquote

from .helper import read_package
from .rights import local_name

ENCRYPTION_PATH = "META-INF/encryption.xml"


@dataclass(frozen=True)
class ObfuscatedFont:
    """A resource the source shipped obfuscated, and how.

    `file_name` is the manifest href — relative to the package document,
    the coordinate system that survives into the output, where the OPF sits
    somewhere else. `uri` is where it was in the *source* container, kept
    for the messages that name it.
    """

    file_name: str
    algorithm: str
    uri: str


IDPF_ALGORITHM = "http://www.idpf.org/2008/embedding"
ADOBE_ALGORITHM = "http://ns.adobe.com/pdf/enc#RC"

CONTAINER_PATH = "META-INF/container.xml"
CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"

MIMETYPE_PATH = "mimetype"

# How much of the file each scheme scrambles, and with what key length.
IDPF_WINDOW = 1040  # 52 rounds of a 20-byte SHA-1 digest
ADOBE_WINDOW = 1024  # 64 rounds of the 16-byte UUID


def _idpf_key(identifier):
    """SHA-1 of the unique identifier with every whitespace character gone.

    None when the identifier is empty. `sha1(b"")` is a computable key and
    a certainly wrong one: a font XORed with it would be counted as
    restored and then re-scrambled under the output identifier — corrupt,
    and silently so. No identifier is no key, exactly as it already is for
    Adobe below.
    """
    stripped = "".join(identifier.split())
    if not stripped:
        return None
    return hashlib.sha1(stripped.encode("utf-8")).digest()


def _adobe_key(identifier):
    """The 16 raw bytes of the identifier's UUID, or None if it has none."""
    hexed = "".join(identifier.split()).replace("urn:uuid:", "").replace("-", "")
    try:
        key = bytes.fromhex(hexed)
    except ValueError:
        return None
    return key if len(key) == 16 else None


def _unscramble(data, key, window):
    """XOR the leading window with the key, cycled. Its own inverse."""
    out = bytearray(data)
    for index in range(min(window, len(out))):
        out[index] ^= key[index % len(key)]
    return bytes(out)


def _child(element, name):
    """The first direct child with this local name, or None."""
    return next((el for el in element if local_name(el.tag) == name), None)


def _declarations(archive):
    """(algorithm, container-relative URI) for every encrypted resource.

    Matched by local name, and `EncryptionMethod` among direct children
    only — the rule `rights.check_epub` gates on. Requiring the xmlenc
    namespace here read *less* than the gate accepted: a producer who wrote
    `EncryptedData` in the container namespace (or in none) cleared the
    gate as font obfuscation and then had nothing restored and nothing
    reported, so the output shipped a scrambled font with no declaration
    left to explain it.

    Direct children throughout, `CipherReference` included: it is read
    from the entry's own `CipherData` and nowhere else. Searching the
    subtree instead picked up references that belong to something else —
    a `KeyInfo/EncryptedKey` wrapping its own cipher text names one, and
    it comes first — so the entry was read as declaring a resource it
    does not, and a nested `EncryptedData` had its URI declared twice.

    The URI is percent-encoded in the file, so it is decoded back to the
    member name here; `build_encryption_xml` encodes on the way out.
    """
    root = ET.fromstring(archive.read(ENCRYPTION_PATH))
    found = []
    for data in root.iter():
        if local_name(data.tag) != "EncryptedData":
            continue
        method = _child(data, "EncryptionMethod")
        cipher_data = _child(data, "CipherData")
        reference = (
            None if cipher_data is None else _child(cipher_data, "CipherReference")
        )
        if method is None or reference is None:
            continue
        uri = reference.get("URI")
        if uri:
            found.append((method.get("Algorithm"), unquote(uri)))
    return found


def deobfuscate_fonts(book, epub_path):
    """Replace every obfuscated item's content with its plain bytes.

    Returns `(restored, unresolved)` — an `ObfuscatedFont` per resource put
    back, which the caller keeps so the same fonts can be scrambled again
    after the output is written, and the URIs that could not be, which the
    caller should say out loud rather than leave the reader to discover as
    a broken font.

    `book` is an already-read ebooklib book whose items still hold the raw
    bytes; it is mutated in place, and the output copies those same item
    objects by reference.
    """
    try:
        with zipfile.ZipFile(epub_path) as archive:
            if ENCRYPTION_PATH not in archive.namelist():
                return [], []
            declarations = _declarations(archive)
    except Exception:
        # check_epub already read this file; anything failing here is a race
        # or a truncation the reader below will report far better.
        return [], []

    # The OCF key is the identifier the package names as its
    # `unique-identifier`, and nothing else. ebooklib's `book.uid` is not a
    # fallback at any price: `EpubBook.reset()` seeds it with a fresh
    # random uuid, so a book that names no identifier arrives carrying an
    # invented one that no reader can tell from a real reading — and when
    # it *is* read, it is the last identified dc:identifier, which on a
    # book listing an ISBN after its UUID is a different string from the
    # named unique-identifier. Either way it is a key that unscrambles
    # nothing while reporting success, which is worse than no key at all.
    # `read_package` answers None for a package it could not read, so that
    # case lands here as no identifier too.
    package = read_package(epub_path)
    opf_dir = package.opf_dir
    identifier = package.unique_identifier or ""

    # A CipherReference URI is relative to the container root, while an
    # ebooklib item's file_name is relative to the OPF. Both spellings are
    # accepted: producers have written each.
    items = {}
    for item in book.get_items():
        name = getattr(item, "file_name", None)
        if not name:
            continue
        items.setdefault(posixpath.normpath(posixpath.join(opf_dir, name)), item)
        items.setdefault(posixpath.normpath(name), item)

    restored, unresolved = [], []
    for algorithm, uri in declarations:
        if algorithm == IDPF_ALGORITHM:
            key, window = _idpf_key(identifier), IDPF_WINDOW
        elif algorithm == ADOBE_ALGORITHM:
            key, window = _adobe_key(identifier), ADOBE_WINDOW
        else:
            # unreachable: check_epub refuses any other algorithm outright
            unresolved.append(uri)
            continue

        item = items.get(posixpath.normpath(uri))
        if key is None or item is None or not getattr(item, "content", None):
            unresolved.append(uri)
            continue
        item.content = _unscramble(item.content, key, window)
        restored.append(
            ObfuscatedFont(file_name=item.file_name, algorithm=algorithm, uri=uri)
        )
    return restored, unresolved


ENCRYPTION_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
    ' xmlns:enc="http://www.w3.org/2001/04/xmlenc#">\n'
    "{entries}"
    "</encryption>\n"
)

ENCRYPTION_ENTRY = (
    "  <enc:EncryptedData>\n"
    '    <enc:EncryptionMethod Algorithm="{algorithm}"/>\n'
    "    <enc:CipherData>\n"
    '      <enc:CipherReference URI="{uri}"/>\n'
    "    </enc:CipherData>\n"
    "  </enc:EncryptedData>\n"
)


def _written_opf_dir(path):
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read(CONTAINER_PATH))
    rootfile = container.find(f".//{CONTAINER_NS}rootfile")
    full_path = rootfile.get("full-path") if rootfile is not None else None
    return posixpath.dirname(full_path or "")


def build_encryption_xml(members):
    """The OCF declaration for `[(container-relative path, algorithm)]`."""
    # The member name goes out as a URI, not as a path: `Old#1.otf`
    # written as is declares a fragment and resolves to nothing, and
    # `a b.otf` is not a legal URI at all — so percent-encode first (`/`
    # stays, it is the path separator), which is exactly what
    # `_declarations` undoes with `unquote` on the way back in. Then
    # escape, because these are XML attribute values: a font called
    # `A&B.otf` is a legal member name and an illegal one to paste in raw.
    # The algorithm is a URI already, and one carrying a real `#`, so it
    # is escaped only.
    entries = "".join(
        ENCRYPTION_ENTRY.format(
            algorithm=escape(algorithm, {'"': "&quot;"}),
            uri=escape(quote(uri, safe="/"), {'"': "&quot;"}),
        )
        for uri, algorithm in members
    )
    return ENCRYPTION_TEMPLATE.format(entries=entries).encode("utf-8")


def reobfuscate_written_epub(path, fonts):
    """Scramble `fonts` inside the epub at `path` and declare them.

    Returns the container-relative members it obfuscated, empty when there
    was nothing to do — in which case the file is not touched at all, so a
    book that shipped no obfuscated font is byte-for-byte what ebooklib
    wrote.

    This runs *after* `write_epub` because ebooklib offers no hook for a
    META-INF member: `encryption.xml` is not in the manifest, so there is no
    item to add. The archive is rebuilt member by member — same order, same
    compression, `mimetype` still first and still stored — with the font
    payloads replaced and the declaration appended.

    The key is the identifier of the book being written, not the source's:
    a translation has its own identity (`derive_translation_identity`), and
    the OCF key is whatever the package the font sits in names as its
    `unique-identifier`. It is read back out of the written package rather
    than taken on trust from the object that was handed to the writer.
    """
    if not fonts:
        return []

    identifier = read_package(path).unique_identifier
    if not identifier:
        # Nothing to derive a key from. Leaving the fonts plain is worse
        # than the alternative only in theory: a package with no usable
        # unique-identifier is broken in ways this cannot repair.
        return []

    opf_dir = _written_opf_dir(path)
    plan = {}
    for font in fonts:
        member = posixpath.normpath(posixpath.join(opf_dir, font.file_name))
        if font.algorithm == IDPF_ALGORITHM:
            key, window = _idpf_key(identifier), IDPF_WINDOW
        elif font.algorithm == ADOBE_ALGORITHM:
            key, window = _adobe_key(identifier), ADOBE_WINDOW
        else:
            continue
        if key is not None:
            plan[member] = (key, window, font.algorithm)

    if not plan:
        return []

    temp_path = f"{path}.reobfuscating"
    try:
        with zipfile.ZipFile(path) as source:
            present = set(source.namelist())
            declared = [
                (member, plan[member][2]) for member in plan if member in present
            ]
            if not declared:
                return []
            with zipfile.ZipFile(temp_path, "w") as target:
                for info in source.infolist():
                    if info.filename == ENCRYPTION_PATH:
                        # ebooklib never writes one; a stale one would lie
                        continue
                    data = source.read(info.filename)
                    if info.filename in plan:
                        key, window, _ = plan[info.filename]
                        data = _unscramble(data, key, window)
                    # the ZipInfo carries the original compress_type, so
                    # `mimetype` stays stored and every member keeps its place
                    target.writestr(info, data)
                target.writestr(ENCRYPTION_PATH, build_encryption_xml(declared))
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return [member for member, _ in declared]
