"""The run's own secrets, kept out of what it prints.

Endpoints quote credentials back — a rejected header is echoed in the error,
and a short key comes back whole — and the CLI prints an endpoint's words in
several places on purpose, because they are usually the only explanation of
what went wrong. So the words are kept and the secrets taken out of them.
"""

import pytest

from book_maker.redaction import MASK, forget_all, redact, remember


@pytest.fixture(autouse=True)
def _clean():
    forget_all()
    yield
    forget_all()


def test_a_registered_value_is_replaced_wherever_it_appears():
    remember("Bearer sk-SECRET-abcdefgh")
    out = redact("bad header 'Bearer sk-SECRET-abcdefgh' rejected")

    assert "sk-SECRET" not in out
    assert MASK in out


def test_the_rest_of_the_message_survives():
    # the endpoint's words are the point of printing it at all
    remember("sk-key-1234567890")
    out = redact("Incorrect API key provided: sk-key-1234567890. See the docs.")

    assert out.startswith("Incorrect API key provided:")
    assert out.endswith("See the docs.")


def test_a_short_value_is_not_registered():
    # blanking every "on" or "1" would destroy the message rather than
    # protect anything
    remember("on")

    assert redact("turn it on") == "turn it on"


def test_a_non_string_is_ignored():
    remember(None, 1, {"a": "b"})

    assert redact("nothing to do here") == "nothing to do here"


def test_an_exception_can_be_passed_directly():
    # every call site holds an exception, not a string
    remember("sk-key-1234567890")

    assert "sk-key" not in redact(ValueError("key sk-key-1234567890 refused"))


def test_nothing_registered_leaves_the_text_alone():
    assert redact("plain message") == "plain message"


class TestTheRunRegistersItsOwn:
    def test_a_key_is_registered_when_the_translator_is_built(self):
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        ChatGPTAPI("sk-registered-1234567890", "Chinese")

        assert MASK in redact("refused sk-registered-1234567890")

    def test_every_key_in_a_rotating_list_is_registered(self):
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        ChatGPTAPI("sk-first-1234567890,sk-second-1234567890", "Chinese")

        assert MASK in redact("refused sk-second-1234567890")

    def test_a_header_value_is_registered_when_extras_are_set(self):
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        t = ChatGPTAPI("sk-test-1234567890", "Chinese")
        t.set_request_extras(extra_headers={"Authorization": "Bearer sk-hdr-abcdefgh"})

        assert MASK in redact("bad header Bearer sk-hdr-abcdefgh")
