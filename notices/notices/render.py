"""A notice, in the words each channel can carry.

NAMED FIELDS, NEVER A SERIALISATION. Every function here reads `Notice.title`,
`Notice.body` and the rest by name and writes lines. There is no `model_dump()`,
no `dict(...)` and no `**` anywhere in this module, which is the rule
`api/mcp/render.py` argues for and `tests/test_mcp_readonly.py` greps for: a
field added to `Notice` next month for one channel's benefit cannot leak into
another channel's output by default.

WHY `quoted` IS A SEPARATE FIELD AND NOT PART OF `body`. It holds text venona
did not write. Today that is a stranger's coverage note from the public access
form -- two thousand characters, unauthenticated -- and a headline out of the
trade press. The current plain-text POST neutralises both by accident: there is
no markup for them to escape into. A Slack block and an HTML email have plenty.
Keeping the untrusted text in its own field is what makes "escape it" a rule a
reader of this file can check, rather than a thing each renderer has to
remember to look for inside a sentence.

`untrust.scrub` runs on it before any of them, and that is a different job from
escaping: it drops every Unicode format character, so injected text cannot be
INVISIBLE to the person reading the message. `untrust.quote` is deliberately not
used -- its `<untrusted>` envelope is a boundary marker for a model, and on a
surface a person reads it would be both meaningless and this repo's vocabulary.
"""

from __future__ import annotations

import html
import json

from notices import untrust
from notices.types import Notice

#: A stranger's paragraph, bounded for a message rather than for a prompt. The
#: access form accepts 2000 characters; nobody wants that in a Slack card.
QUOTE_CHARS = 500

#: ntfy reads a `Title` header and truncating it is better than a rejected POST.
TITLE_CHARS = 120


def _clean(text: str | None) -> str:
    return untrust.scrub(text or "", limit=QUOTE_CHARS)


def _url(notice: Notice, base: str) -> str | None:
    """The link as somewhere a person can actually click.

    A relative path is no use in an email or a Slack card, and `base` is the
    instance's own public URL, so a notice can carry `/admin/runs` and let the channel
    decide. No base means no link rather than a broken one.
    """
    if not notice.link:
        return None
    if notice.link.startswith("http://") or notice.link.startswith("https://"):
        return notice.link
    if not base:
        return None
    return base.rstrip("/") + notice.link


def as_text(notice: Notice, *, base: str = "") -> str:
    """Plain text, for ntfy, healthchecks.io and anything else that takes a POST.

    The shape `notify.alert` has always sent: a `venona:` prefix so a message in
    a shared channel says who is talking, then the body. Nothing here can be
    escaped INTO, so the scrub is the whole defence and is enough.
    """
    lines = [f"venona: {notice.title}"]
    for paragraph in notice.body:
        if paragraph.strip():
            lines.append("")
            lines.append(paragraph.strip())
    quoted = _clean(notice.quoted)
    if quoted:
        lines.append("")
        # Quote-indented, so a reader can see where venona stops talking.
        lines.append("> " + quoted)
    url = _url(notice, base)
    if url:
        lines.append("")
        lines.append(f"{notice.link_label or 'Open'}: {url}")
    return "\n".join(lines)


def as_slack(notice: Notice, *, base: str = "") -> str:
    """A Slack (or Discord) JSON body.

    `text` is set as well as `blocks` because it is what a notification preview
    and a screen reader get, and a card with no fallback text arrives on a phone
    as an empty message.

    EVERY BLOCK IS `plain_text`, INCLUDING VENONA'S OWN. Slack's `mrkdwn` would
    interpret `<https://…|click me>` inside a stranger's coverage note as a
    link, which is a phishing link in a message the reader trusts because
    venona sent it. Turning markdown off for one field and leaving it on for
    another is the kind of distinction that survives exactly one refactor.
    """
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": notice.title[:150], "emoji": False},
        }
    ]
    for paragraph in notice.body:
        if paragraph.strip():
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "plain_text", "text": paragraph.strip(), "emoji": False},
                }
            )
    quoted = _clean(notice.quoted)
    if quoted:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "plain_text", "text": quoted, "emoji": False}],
            }
        )
    url = _url(notice, base)
    if url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": notice.link_label or "Open",
                            "emoji": False,
                        },
                        "url": url,
                    }
                ],
            }
        )
    return json.dumps({"text": as_text(notice, base=base), "blocks": blocks})


def as_email(notice: Notice, *, base: str = "") -> tuple[str, str, str]:
    """`(subject, text, html)`.

    Both bodies, always. A text alternative is not a courtesy: a client that
    refuses HTML shows the text part, and one that has neither shows nothing.

    THE COLOURS ARE LITERAL HERE AND THAT IS NOT A TOKEN VIOLATION. Every tier-2
    token in `web/src/styles/tokens.css` is a `light-dark()` pair, and no email
    client resolves either custom properties or that function, so a stylesheet
    built from the tokens would arrive as unstyled text. This is the same
    boundary `extension/scripts/emit-styles.mjs` crosses for the extension: past
    the edge of the cascade, values get resolved. Kept to two greys and a rule
    so there is no palette here to drift out of step with the app's.
    """
    subject = f"Venona: {notice.title}"
    text = as_text(notice, base=base)
    url = _url(notice, base)

    parts = [
        "<h1 style='font:600 18px/1.3 system-ui,sans-serif;margin:0 0 16px'>"
        f"{html.escape(notice.title)}</h1>"
    ]
    for paragraph in notice.body:
        if paragraph.strip():
            parts.append(
                "<p style='font:14px/1.6 system-ui,sans-serif;color:#1c1c1c;margin:0 0 12px'>"
                f"{html.escape(paragraph.strip())}</p>"
            )
    quoted = _clean(notice.quoted)
    if quoted:
        # `html.escape` on scrubbed text, and the order matters: scrub first so
        # nothing invisible survives, escape second so nothing structural does.
        parts.append(
            "<blockquote style='font:14px/1.6 system-ui,sans-serif;color:#555;"
            "border-left:2px solid #ddd;margin:0 0 12px;padding:0 0 0 12px'>"
            f"{html.escape(quoted)}</blockquote>"
        )
    if url:
        parts.append(
            f"<p style='font:14px/1.6 system-ui,sans-serif;margin:16px 0 0'>"
            f"<a href='{html.escape(url, quote=True)}' style='color:#1a5fb4'>"
            f"{html.escape(notice.link_label or 'Open')}</a></p>"
        )
    body_html = (
        "<div style='max-width:38rem;margin:0 auto;padding:24px'>" + "".join(parts) + "</div>"
    )
    return subject, text, body_html


def title_header(notice: Notice) -> str:
    """ntfy reads the message title from a header rather than the body."""
    return notice.title[:TITLE_CHARS]
