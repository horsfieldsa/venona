---
status: accepted
date: 2026-08-15
---

# 2026-08-15 — The extension points at the Vite proxy, and Vite is pinned to IPv4


Two bugs that only exist together.

**One setting, two jobs.** `baseUrl` is where the extension's requests go and,
with `/api` stripped in `background.ts::appUrl`, where the card's
"Open in venona →" points. Those are one origin in production, where the reverse proxy
serves the SPA and the API together. They are not one origin in development:
uvicorn on :8000 has no frontend, so every card's link landed on a 404 from a
server that could never have answered it. The default is now
`http://127.0.0.1:5173/api` — the Vite proxy is the origin that is both, and
`changeOrigin: false` means the API still sees the extension's own `Origin`, so
nothing about C12 or `host_permissions` changes. Both ports stay in
`host_permissions`; pointing straight at the API still works, you just lose the
link.

**And that default was dead on arrival**, because Vite's `server.host` defaults
to the string `localhost`, which node 17+ hands to the resolver — landing on
`[::1]`. The dev server listened on IPv6 loopback only and `http://127.0.0.1:5173`
was CONNECTION REFUSED. Nothing noticed for as long as it did because a browser
retries the other family silently, and every doc says `localhost:5173`. A client
that names one literal address gets no such retry, and an extension is exactly
that: `host_permissions` and the API base are literal strings. `vite.config.ts`
now pins `host: '127.0.0.1'`, which is the literal the rest of the repo uses.

`ext_smoke.py` probes :5173 for the same reason — a smoke run against uvicorn
direct would be testing a configuration nobody runs.
