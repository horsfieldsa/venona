"""SQL lives here and nowhere else -- the instance's `db/repositories/` rule,
held by the same sweep (`make lint-repositories`). A repository takes `conn`
first, returns rows or plain values, never reads a Request or Settings, and
never decides authorization.
"""
