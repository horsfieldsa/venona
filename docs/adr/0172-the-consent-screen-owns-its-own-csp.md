---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — The consent screen owns its own CSP, because form-action follows the redirect

The Authorize button did nothing. The console said:

```
Sending form data to 'https://defense.example.com/api/oauth/authorize' violates
the following Content Security Policy directive: "form-action 'self'".
```

The URL in that message is same-origin, which is what makes it so misleading:
the form's action is not what was refused. **Chrome checks `form-action`
against every hop of the submission**, and this form's second hop is the 302 to
the client's callback, which is `http://localhost:<port>/…` or a
`chromiumapp.org` URL. Under a flat `form-action 'self'` the browser blocks it
and then names the innocent URL.

Verified rather than reasoned about, because the console message argues for the
wrong diagnosis. An isolated page under `form-action 'self'` submits fine to a
same-origin 302 and is blocked by a cross-origin one; the same page under
`form-action 'self' http://127.0.0.1:9999` follows it.

**The page carries its own policy, and only that directive moves.**
`content_security_policy(form_action=…)` is the one policy in one place;
everything else on the consent screen stays exactly what the rest of the origin
gets. The widening names one origin: the destination already printed on the page
for the human to look at, so the policy can never permit more than what is being
approved.

**Rebuilt from parts, never interpolated.** `redirect_uri` is attacker-supplied
— registration is unauthenticated by RFC 7591 — and a header built by
concatenation is a header-injection primitive. Only scheme, host and port are
taken, so nothing with a space, semicolon or newline in it can reach the policy.

**Two policies are worse than one.** A browser enforces the intersection of
every CSP on a response, so appending the edge policy over the route's would
undo the widening while both headers looked correct in isolation. The middleware
therefore defers when a response already carries one, and the reverse proxy config
does the same with a set-if-absent `Content-Security-Policy` (set only if absent).
`test_the_edge_defers_to_a_policy_a_route_set_for_itself` fails with either half
removed.
