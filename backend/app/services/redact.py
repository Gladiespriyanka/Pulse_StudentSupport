"""Mask contact details / ID numbers students may paste into free text, before storage."""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ID12 = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")  # Aadhaar-like
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)|(?<!\d)\d{10}(?!\d)")


def redact_pii(text: str) -> tuple[str, int]:
    n = 0

    def sub(rx, token, s):
        nonlocal n
        s, k = rx.subn(token, s)
        n += k
        return s

    text = sub(_EMAIL, "[email]", text)
    text = sub(_ID12, "[id-number]", text)
    text = sub(_PHONE, "[phone]", text)
    return text, n
