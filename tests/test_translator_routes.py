"""Route wiring for the non-OpenAI formats.

These cover what the CLI promises each `--api_format` on its way into a
translator: where the endpoint URL comes from, and that a comma-separated
`--key` really rotates rather than being sent verbatim as one credential.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from anthropic import AuthenticationError as AnthropicAuthError
from anthropic import NotFoundError as AnthropicNotFound

from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.claude_translator import Claude
from book_maker.translator.custom_api_translator import CustomAPI


class TestCustomAPIEndpoint:
    """`--api_format customapi --api_base URL` is the documented route."""

    def test_the_endpoint_comes_from_api_base(self):
        translator = CustomAPI("", "Japanese", api_base="https://host/translate")

        with (
            patch("book_maker.translator.custom_api_translator.requests.post") as post,
            patch("book_maker.translator.custom_api_translator.time.sleep"),
        ):
            post.return_value = SimpleNamespace(text='{"data": "訳"}')
            assert translator.translate("text") == "訳"

        assert post.call_args.kwargs["url"] == "https://host/translate"

    def test_no_endpoint_fails_loud(self):
        # posting to "" is the silent version of this, and it used to be
        # exactly what the documented invocation did
        with pytest.raises(ValueError, match="endpoint URL"):
            CustomAPI("", "Japanese")

    def test_source_lang_reaches_the_request(self):
        import json

        translator = CustomAPI(
            "", "Japanese", api_base="https://host/t", source_lang="English"
        )

        with (
            patch("book_maker.translator.custom_api_translator.requests.post") as post,
            patch("book_maker.translator.custom_api_translator.time.sleep"),
        ):
            post.return_value = SimpleNamespace(text='{"data": "訳"}')
            translator.translate("text")

        assert json.loads(post.call_args.kwargs["data"])["source_lang"] == "English"


class TestAnthropicWrongShape:
    """A gateway asked for the anthropic shape it does not serve says so."""

    def _claude(self, side_effect, api_base="https://gw.example.com"):
        with patch("book_maker.translator.claude_translator.Anthropic"):
            translator = Claude("k", "zh-hans", api_base=api_base)
        translator.client.messages.create = Mock(side_effect=side_effect)
        return translator

    def _not_found(self):
        return AnthropicNotFound(
            "not found",
            response=httpx.Response(
                404, request=httpx.Request("POST", "https://gw.example.com/v1/messages")
            ),
            body=None,
        )

    @pytest.mark.parametrize(
        "api_base", ["https://gw.example.com", "https://notanthropic.com"]
    )
    def test_a_404_from_a_gateway_names_the_fix(self, api_base):
        translator = self._claude(self._not_found(), api_base=api_base)

        with pytest.raises(RuntimeError, match="--api_format openai"):
            translator.translate("text")

    def test_an_auth_failure_is_not_about_the_shape(self):
        error = AnthropicAuthError(
            "bad key",
            response=httpx.Response(
                401, request=httpx.Request("POST", "https://gw.example.com/v1/messages")
            ),
            body=None,
        )
        translator = self._claude(error)

        with pytest.raises(AnthropicAuthError):
            translator.translate("text")

    @pytest.mark.parametrize("api_base", [None, "https://api.anthropic.com"])
    def test_a_404_on_anthropics_own_host_is_about_the_model(self, api_base):
        translator = self._claude(self._not_found(), api_base=api_base)

        with pytest.raises(AnthropicNotFound):
            translator.translate("text")


class TestBaseNormalization:
    def test_an_sdk_route_loses_a_pasted_request_path(self):
        from book_maker.cli import normalize_api_base

        assert (
            normalize_api_base("https://h/v1/chat/completions", "openai")
            == "https://h/v1"
        )
        assert normalize_api_base("https://h/v1/", "openai") == "https://h/v1"

    def test_a_literal_endpoint_is_left_exactly_as_given(self):
        # customapi posts to this URL itself; trimming a path it needs would
        # send every request somewhere else
        from book_maker.cli import normalize_api_base

        for url in (
            "https://h/completions",
            "https://h/messages",
            "https://h/translate/",
        ):
            assert normalize_api_base(url, "customapi") == url


class TestKeyRotation:
    """`--key a,b` is advertised for every format that takes a key."""

    def test_claude_sends_one_key_not_the_list(self):
        with patch("book_maker.translator.claude_translator.Anthropic") as client:
            translator = Claude("first,second", "zh-hans")

        assert client.call_args.kwargs["api_key"] == "first"

        translator.rotate_key()
        assert translator.client.api_key == "second"
        translator.rotate_key()
        assert translator.client.api_key == "first"  # cycles

    def test_caiyun_sends_one_key_not_the_list(self):
        translator = Caiyun("first,second", "english")

        assert translator.headers["x-authorization"] == "token first"

        translator.rotate_key()
        assert translator.headers["x-authorization"] == "token second"

    def test_caiyun_rotates_per_translation(self):
        translator = Caiyun("first,second", "english")
        translator.headers["x-authorization"] = "token stale"

        with patch("book_maker.translator.caiyun_translator.requests.request") as req:
            req.return_value = SimpleNamespace(json=lambda: {"target": ["translated"]})
            translator.translate("text")

        # the header the request actually carried, not just the final state
        assert req.call_args.kwargs["headers"]["x-authorization"] == "token second"
