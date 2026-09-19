"""xAI's endpoint.

The OpenAI shape at api.x.ai, so this is the OpenAI route with that address
and xAI's key variable. See groq_translator for why no model id is named
here.
"""

from book_maker.translator.chatgptapi_translator import ChatGPTAPI

XAI_API_BASE = "https://api.x.ai/v1"


class XAIClient(ChatGPTAPI):
    DEFAULT_API_BASE = XAI_API_BASE
