"""Whether an EPUB declares a technical protection measure.

Circumventing one is unlawful regardless of what the reader owns (DMCA
§1201, EUCD Art. 6, Japan's Copyright Act Art. 30-1), so a protected book
is refused outright and no flag opens it. This module only *detects*; it
holds no key material and no decryption of any kind, and it must stay that
way.

Stdlib only, deliberately: the check runs before the book is handed to any
reader, so it cannot depend on one.
"""

import xml.etree.ElementTree as ET
import zipfile

DRM_MESSAGE = (
    "This EPUB is protected by DRM and cannot be translated; "
    "the tool does not and will not remove it."
)

# A vendor rights file is protection by its mere presence: Adobe ADEPT and
# the Nook variant (rights.xml), Readium LCP (license.lcpl), Apple FairPlay
# (sinf.xml). META-INF/signatures.xml is deliberately absent — a signature
# asserts integrity, not protection, and translating a book invalidates it
# the way any edit would.
PROTECTION_FILES = frozenset(
    {
        "META-INF/rights.xml",
        "META-INF/license.lcpl",
        "META-INF/sinf.xml",
    }
)

ENCRYPTION_FILE = "META-INF/encryption.xml"

# encryption.xml is also where font obfuscation is declared, which is not
# protection: it scrambles an embedded font so the foundry's licence is
# honoured, and the content documents stay in the clear. Everything else
# named there (xmlenc#aes128-cbc, aes256-cbc, …) encrypts content.
FONT_OBFUSCATION = frozenset(
    {
        "http://www.idpf.org/2008/embedding",
        "http://ns.adobe.com/pdf/enc#RC",
    }
)


def local_name(tag):
    """The element name without its namespace.

    Matching by local name is deliberate. The two elements below are only
    ever written in the xmlenc namespace by a conforming producer, but a
    check that trusts the namespace lets a non-conforming one hide an AES
    declaration in plain sight — and this check exists precisely for files
    that are not playing fair.

    `font_obfuscation` reads the same file by the same rule: a declaration
    this gate accepted as font obfuscation has to be one the read path can
    see, or the font stays scrambled with nothing said about it.
    """
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def check_epub(path):
    """Return "drm" if `path` declares a protection measure, else "ok".

    A file that is not a zip, or is not there at all, comes back "ok". That
    is not a claim that it is a clean EPUB — it is a refusal to report an
    error that belongs to someone else: the reader that opens the book next
    names the actual problem ("not a zip file", "no such file") far better
    than a rights check could, and a check that raised here would replace a
    good message with a confusing one.

    An `encryption.xml` that cannot be parsed comes back "drm". A
    declaration that cannot be read cannot be read as harmless.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if names & PROTECTION_FILES:
                return "drm"
            if ENCRYPTION_FILE not in names:
                return "ok"
            try:
                declaration = archive.read(ENCRYPTION_FILE)
            except Exception:
                return "drm"
    except (OSError, zipfile.BadZipFile):
        return "ok"

    try:
        root = ET.fromstring(declaration)
    except ET.ParseError:
        return "drm"

    # Allow-list, not deny-list: the file is clear only if every declared
    # resource is positively identified as font obfuscation. An entry that
    # names no algorithm, names one this code does not know, or hides the
    # method somewhere the reader did not look is protection as far as this
    # tool is concerned — the cost of being wrong the other way is helping
    # circumvent something.
    entries = [el for el in root.iter() if local_name(el.tag) == "EncryptedData"]
    if not entries:
        # A declaration that declares nothing is malformed, and malformed
        # fails closed exactly like an unparsable one.
        return "drm"
    for entry in entries:
        # Direct children only. `EncryptionMethod` says how *this* resource
        # is encrypted, and the one place it can say that is immediately
        # under its EncryptedData — a copy parked deeper (under CipherData,
        # say) declares nothing, and reading it as a declaration is how a
        # font algorithm quoted in the wrong place clears an AES-encrypted
        # book.
        algorithms = [
            el.get("Algorithm")
            for el in entry
            if local_name(el.tag) == "EncryptionMethod"
        ]
        if not algorithms:
            return "drm"
        if any(algorithm not in FONT_OBFUSCATION for algorithm in algorithms):
            return "drm"
    return "ok"
