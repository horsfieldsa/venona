# Third-party notices

Venona's own code is MIT licensed (see `LICENSE`). A few things it ships or
depends on carry their own terms.

## Fonts

Inter (The Inter Project Authors) and JetBrains Mono (The JetBrains Mono
Project Authors) are licensed under the SIL Open Font License 1.1. The hub
ships copies in `hub/hub/static/fonts/`, with the licence texts beside them.
The web app and the browser extension bundle the same fonts from the
`@fontsource-variable` npm packages at build time.

## Neo4j

The graph runs on Neo4j Community Edition, which is GPLv3. It runs as its own
container (`compose.yaml`) and Venona talks to it over the network through the
Apache-2.0 `neo4j` Python driver. No Neo4j code is included in this
repository.

## Python dependencies with weak copyleft or file-level licences

- `psycopg`, `psycopg-binary` and `psycopg-pool`: LGPL-3.0, used unmodified as
  libraries.
- `certifi`: MPL-2.0.
- `tld`: MPL-1.1 / GPL-2.0 / LGPL-2.1, used under MPL-1.1.

## JavaScript build tooling

- `lightningcss`: MPL-2.0, a build-time dependency only.

Everything else in `uv.lock` and the `package-lock.json` files is under a
permissive licence (MIT, BSD, Apache-2.0, ISC or similar).
