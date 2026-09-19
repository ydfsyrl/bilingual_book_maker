"""One place to keep the run's own secrets out of what it prints.

Endpoints quote credentials back. OpenAI's 401 is the plain example —
`Incorrect API key provided: sk-...` — and the CLI prints an endpoint's
words in several places by design, because they are usually the only
explanation of what went wrong. So the words are kept and the secrets are
taken out of them, rather than the other way round.

Only values this run was *given* are removed: a key from `--key` or the
environment, and any `--extra_headers` value. Everything else is the
endpoint's own message and is printed unchanged.
"""

_SECRETS = set()

MASK = "<redacted>"
# Below this, a "secret" is something like "1" or "on" and blanking every
# occurrence of it would destroy the message instead of protecting anything.
_MIN_LENGTH = 8


def remember(*values):
    """Register values that must never be echoed back."""
    for value in values:
        if isinstance(value, str) and len(value.strip()) >= _MIN_LENGTH:
            _SECRETS.add(value.strip())


def redact(text):
    """`text` with every registered secret replaced by `MASK`."""
    text = str(text)
    for secret in _SECRETS:
        if secret in text:
            text = text.replace(secret, MASK)
    return text


def forget_all():
    """Test hook: drop everything registered."""
    _SECRETS.clear()
