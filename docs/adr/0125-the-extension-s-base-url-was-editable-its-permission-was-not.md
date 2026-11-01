---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — The extension's base URL was editable; its permission was not


`host_permissions` named the two localhost ports and nothing else, so pointing
the extension at a deployed venona could not work however correctly the URL was
typed. `host_permissions` is what grants an extension cross-origin access
(measured, 2026-08-10) — the setting decides where requests GO, the permission
decides whether they can be made at all. The symptom was
`TypeError: Failed to fetch`, and nothing in the options page connected an
editable field to a permission it never asked for.

Two halves to the fix, and they are for different cases:

**`https://example.com/*` is declared.** It is *the* deployment rather than an
arbitrary host, so it is in `host_permissions` and needs no prompt — the
expected configuration is the frictionless one. It is offered as a one-click
preset beside `Local dev`, because typing a URL from memory is how you get a
silent typo. `client.spec.ts` reads the manifest and asserts every preset is
covered by it: renaming the host in one file and not the other would turn a
button documented as never prompting into one that prompts, or that silently
cannot reach anything.

**Any other host is asked for on save**, through `optional_host_permissions`
and `originPatternFor`. A declined prompt still saves the URL — the user may be
about to grant it from `chrome://extensions`, and refusing to remember what they
typed helps nobody — but the status line says requests will fail until it is
granted, rather than letting them fail later as "could not reach".

**The default stays `http://127.0.0.1:5173/api`.** Making production the default
is the obvious move for a shipped artifact and it is wrong here for two reasons:
`scripts/ext_smoke.py` never sets `baseUrl` and depends on it, and a development
checkout that silently pointed at production would annotate against the live
corpus without ever saying so. One click is a small enough price for neither of
those.

*Footnote, because it cost a build:* `optional_host_permissions`' pattern cannot
be written inside a block comment. It contains `*/`, which ends the comment, and
the resulting parse errors point at the line after the docstring rather than at
the string that caused them.
