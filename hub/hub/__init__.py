"""The hub: directory, portal, SSO and management channel for venona instances.

A separate package on purpose -- no `venona.*` imports anywhere under this
tree (docs/adr/0141). The hub speaks to instances over two wire contracts and
nothing else: it IS the OIDC issuer the instance's `VENONA_OIDC_*` points at,
and it PULLS through the instance's `vmgmt_`-scoped management surface
(docs/adr/0142). Both contracts are pinned by tests/hub/, including one that
imports the instance's claim parser to prove a hub-minted id_token satisfies
it.
"""
