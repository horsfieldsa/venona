"""Making text venona did not write safe to show, wherever it is shown.

TWO PLACES NEED THE SAME SCRUB, which is why it is here rather than beside
either of them. A stranger's paragraph reaches a model as retrieved evidence and
reaches a person as the quoted half of a notice, and the hazard that matters for
BOTH is the same one: Unicode format characters make injected text invisible to
whoever is reading it. A reviewer cannot review what they cannot see, and an
operator cannot judge an access request whose self-description is hiding half
its content.

WHAT THIS DOES AND DOES NOT DO. It moves the odds and closes nothing against a
model. The category-`Cf` strip is the part that is not probabilistic: it removes
the class rather than a hand-listed range that drifts as new format characters
are assigned. Escaping for a particular destination is `render`'s job and each
channel's own; this only ever returns plain text.

NO DEPENDENCIES, and no imports from either service. `venona.untrust` builds its
`<untrusted>` envelope on top of this for the model-facing half, and the notice
channels use it directly.
"""

from __future__ import annotations

import re
import unicodedata

MAX_QUOTE_CHARS = 600

# C0 and C1 controls, keeping nothing -- newlines are collapsed separately below.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# A forged delimiter. Rewritten to a visually similar non-ASCII form rather than
# deleted, so the payload stays readable and auditable while being unable to
# close the envelope. Deleting it would make the tool lie about what the corpus
# says, which is its own failure.
#
# PUBLIC, because `services/config_service.py` refuses a prompt document
# containing one. Two spellings of "what an envelope delimiter looks like" is how
# the scrubber and the validator come to disagree about it.
DELIMITER = re.compile(r"<(/?)untrusted", re.IGNORECASE)


def scrub(text: str, *, limit: int | None = MAX_QUOTE_CHARS, keep_lines: bool = False) -> str:
    """Sanitise without silently discarding meaning.

    `limit=None` means the caller has already bounded the text and owns HOW --
    `llm/runner.py` truncates a document head+tail because the closing paragraphs
    of an article carry the award value, and a tail-drop here would throw away
    exactly the part it went to the trouble of keeping.
    """
    # Every Unicode format character (category Cf) goes: the zero-widths, the
    # bidirectional overrides and isolates, the word joiner, soft hyphen, the Tags
    # block. These are what make injected text invisible to a human reading the
    # transcript, and dropping the whole category closes the class rather than a
    # hand-listed range that drifts as new format characters are assigned.
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    cleaned = _CONTROL.sub(" ", cleaned)
    if keep_lines:
        # A whole document, where paragraphs are meaning: the analyze stage reads
        # structure and the acronym stage quotes sentences, so flattening one to a
        # single line degrades extraction. Horizontal whitespace still collapses,
        # and runs of blank lines cap at one -- what survives is the shape a
        # reader would recognise, not the author's exact spacing.
        cleaned = re.sub(r"[^\S\n]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    else:
        # Newlines collapse to spaces: a forged closing tag on a line of its own is
        # the classic shape, and nothing venona quotes needs internal line breaks.
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # The breakout defence, and it does not depend on the collapse above: a forged
    # delimiter is rewritten wherever it sits, on its own line or mid-sentence.
    # That is what makes `keep_lines` safe rather than a concession.
    cleaned = DELIMITER.sub(lambda m: f"‹{m.group(1)}untrusted", cleaned)
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "…"
    return cleaned
