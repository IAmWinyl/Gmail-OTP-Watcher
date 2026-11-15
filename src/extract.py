"""Finding the sign-in code in an email: decode it, turn it into the visual
blocks a person would see, and pick the code that stands alone next to
verification wording."""
import html as html_lib
import re
import unicodedata
from email.header import decode_header, make_header


def decode_hdr(value):
    """Decode an RFC2047-encoded header (e.g. =?UTF-8?B?...?=) to plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def to_blocks(text):
    """Split text into its visual blocks (one per non-empty line), with
    whitespace collapsed and invisible characters (zero-width joiners etc.)
    dropped so they can't stop a block from reading as a bare code."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text
                   if ch == "\n" or not unicodedata.category(ch).startswith("C"))
    return [b for b in (re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")) if b]


# Words that only show up around sign-in codes. Generic labels like "code:",
# "confirmation code" or "authorization code" are deliberately absent: DMV
# appointments and card receipts print those right above their own numbers.
# A bare "log in" is absent too ("Log in to your account" is in every receipt).
VERIFY_KW = re.compile(
    r"verif|passcode|one[\s-]?time|\botp\b|authenticat|\b2fa\b"
    r"|two[\s-]?(?:factor|step)|multi[\s-]?factor"
    r"|(?:sign[\s-]?in|log[\s-]?in|login)\s+code",
    re.IGNORECASE,
)

# A whole block that is nothing but a code: 4-8 digits, or 4-8 uppercase
# letters/digits with at least one of each (Steam's "6PVGY"), so a shouty word
# like TYPEFIRE never qualifies.
CODE_BLOCK = re.compile(r"\d{4,8}|(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{4,8}")

# How many blocks a code may sit from its verification wording. Real ones are
# directly above or below it ("Your one-time passcode:" then the code).
KW_REACH = 2


def extract_code(subject, bodies):
    """Pull a verification code out of an email, or None.

    Senders put sign-in codes on their own: a line of their own in plain text,
    a bold paragraph or table cell of their own in HTML. Receipts, coupons and
    appointment emails put theirs inline ("Auth code: 015016"). So a code has
    to be a whole block by itself, with verification wording in a nearby block.
    Each body (plain and HTML) is checked separately, since a sender can put
    the code alone in one and mid-sentence in the other.
    """
    subject_block = [" ".join(to_blocks(subject))] if subject else []

    best = None
    for body in bodies:
        blocks = subject_block + to_blocks(body)
        kw = [i for i, b in enumerate(blocks) if VERIFY_KW.search(b)]
        if not kw:
            continue
        for i, block in enumerate(blocks):
            if not CODE_BLOCK.fullmatch(block):
                continue
            dist = min(abs(i - k) for k in kw)
            if dist > KW_REACH:
                continue
            # Closest wording wins; tie-break toward 6-digit codes, then position
            score = (dist, 0 if len(block) == 6 else 1, i)
            if best is None or score < best[0]:
                best = (score, block)
    return best[1] if best else None


# Tags that start a new visual block. Everything else (strong, span, a, ...)
# is inline, so a code wrapped in <strong> still reads as a block of its own.
_BLOCK_TAGS = (r"p|div|td|th|tr|li|ul|ol|table|tbody|thead|h[1-6]|br|hr"
               r"|center|blockquote|section|article|header|footer|title")


def html_to_text(raw):
    """HTML to plain text with one visual block per line."""
    raw = re.sub(r"<(script|style|head)\b.*?</\1\s*>", "\n", raw,
                 flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.DOTALL)
    raw = re.sub(rf"</?(?:{_BLOCK_TAGS})\b[^>]*>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html_lib.unescape(raw)


def get_bodies(mime_msg):
    """Every non-empty text part of an email as plain text, HTML parts
    converted with one visual block per line.

    Blank parts are skipped: senders sometimes include an EMPTY text/plain part
    alongside the real HTML.
    """
    bodies = []
    for part in mime_msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)  # decode transfer-encoding
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        txt = payload.decode(charset, errors="replace")
        if ctype == "text/html":
            txt = html_to_text(txt)
        if txt.strip():
            bodies.append(txt)
    return bodies
