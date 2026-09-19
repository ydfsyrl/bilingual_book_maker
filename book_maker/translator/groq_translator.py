"""Groq's endpoint.

Groq serves the OpenAI shape at its own address, so the route is the OpenAI
one with that address: the structured-output ladder, session context,
batching, the model check and the usage meter all apply unchanged. Only the
address and the key variable are Groq's.

No default model. Groq's catalogue turns over — the ids in the old preset
list are all retired by now — so naming one here would send a book to a
model nobody chose; `--model` is required, and Groq reports an id it no
longer serves.
"""

from book_maker.translator.chatgptapi_translator import ChatGPTAPI

GROQ_API_BASE = "https://api.groq.com/openai/v1"


class GroqClient(ChatGPTAPI):
    DEFAULT_API_BASE = GROQ_API_BASE
