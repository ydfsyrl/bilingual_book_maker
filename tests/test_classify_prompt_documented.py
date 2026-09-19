"""Pin the classify prompt, and keep its review doc in step with it.

Same two-guard shape as test_prompts_documented.py: the literal below is
tracked and runs in CI, so a prompt edit shows up as a diff a reviewer can
read; the doc half checks the copy humans argue over, and skips where
.gitignore keeps that copy local.
"""

from pathlib import Path

import pytest

from book_maker.loader.classify.model import PAGE_SIZE, VERDICTS, build_prompt

DOC = (
    Path(__file__).resolve().parents[1]
    / "docs/260829-eval-CLASSIFY_PROMPT_FOR_REVIEW.md"
)

CANDIDATE = {
    "key": "block:p.calibre_13",
    "units": 286,
    "chars": 188081,
    "pct": 92.9,
    "mean_chars": 657.6,
    "samples": ["Any fairminded person…"],
}

# The instruction block, exactly as sent — everything before the numbered
# candidates. Editing the prompt means editing this in the same commit.
INSTRUCTIONS = """\
You are preparing a bilingual EPUB. For each content signature below, decide whether it is better to translate its text or keep it as is.
Answer "translate" for book content a reader wants translated: prose, verse, dialogue, headings, captions.
Answer "skip" for text to keep as is: running heads, page or line numbers, manuscript sigla, cross-reference labels, publisher boilerplate, decorative markers.
Answer "unsure" only if the samples genuinely do not settle it. When they are merely thin, prefer translate: translating something unnecessary is cheap, losing content is not.
If the samples show more than one kind of content, answer translate — a signature verdict applies to every occurrence, and there is no per-occurrence override.
A "block:" signature is a block of text of that shape. An "inline:" signature is markup *inside* a sentence; skipping it leaves its text in place, untranslated, and splits the sentence around it — so skip one only when it is genuinely apparatus.\
"""

# How one candidate is rendered under the instructions.
CANDIDATE_BLOCK = """\
1. "block:p.calibre_13" — 286 occurrence(s), 188081 chars (92.9% of the book), mean 657.6 chars
   Sample: Any fairminded person…\
"""


def test_the_prompt_is_what_this_file_says_it_is():
    """The prompt changed, so this literal must change with it, in the same
    commit. Read the diff of both together before approving it."""
    assert build_prompt([CANDIDATE]) == f"{INSTRUCTIONS}\n\n{CANDIDATE_BLOCK}"


def test_the_verdicts_the_prompt_asks_for_are_the_ones_it_accepts():
    """The coupling that silently discards a whole classification pass: the
    prompt names three answers in prose, and VERDICTS is what will be
    accepted back."""
    assert VERDICTS == ["translate", "skip", "unsure"]
    for verdict in VERDICTS:
        assert f'"{verdict}"' in INSTRUCTIONS


def test_the_page_size_is_what_the_prompt_was_written_for():
    """Instructions are phrased for a page of candidates; changing this
    changes how many the model weighs against each other at once."""
    assert PAGE_SIZE == 12


# ---- the reviewable copy, checked only where it exists ---------------------


def _doc_text() -> str:
    if not DOC.exists():
        # A dated docs/ note, kept local by .gitignore. The literals above are
        # what pins the prompt in CI; this half pins the copy humans revise.
        pytest.skip(f"{DOC.name} is not in this checkout (local-only doc)")
    return " ".join(DOC.read_text(encoding="utf-8").split())


def test_every_instruction_line_is_documented():
    haystack = _doc_text()
    for line in INSTRUCTIONS.split("\n"):
        assert " ".join(line.split()) in haystack, (
            f"this instruction is sent to the model but is not in "
            f"{DOC.name}:\n  {line[:120]}..."
        )


def test_the_paging_size_is_stated_correctly():
    assert f"PAGE_SIZE = {PAGE_SIZE}" in _doc_text()


def test_the_verdicts_are_listed():
    haystack = _doc_text()
    for verdict in VERDICTS:
        assert f"`{verdict}`" in haystack


def test_the_doc_says_where_the_prompt_lives():
    assert "book_maker/loader/classify/model.py" in _doc_text()
