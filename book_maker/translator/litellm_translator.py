"""A LiteLLM proxy.

`litellm --config ...` serves the OpenAI shape on port 4000 and fans out to
whatever backends its own config names, so from here it is the OpenAI route
pointed at that proxy. The model id is the one the proxy's config gives the
backend, which is why none is named here.

The default address is the proxy's own default, on this machine; a proxy
somewhere else is `--api_base`. A local address authenticates nobody, so no
key is needed until the proxy is remote or has a master key set.
"""

from book_maker.translator.chatgptapi_translator import ChatGPTAPI

LITELLM_API_BASE = "http://localhost:4000"


class liteLLM(ChatGPTAPI):
    DEFAULT_API_BASE = LITELLM_API_BASE
