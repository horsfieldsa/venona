#!/usr/bin/env bash
# The webhook lane of `make dev-hub`.
#
# NEVER EXITS NON-ZERO, and that is the whole reason this is a script rather
# than a command in the Makefile. `concurrently -k` kills every other lane the
# moment one of them dies, so a missing Stripe CLI -- on a fresh checkout, on a
# machine that has never taken a payment -- would take the hub, the API, the UI
# and a separate front site down with it. Billing is one part of this stack and
# must not be able to stop the rest of it starting.
#
# It also checks the SECRET, which is the failure worth catching early: the
# signing secret `stripe listen` prints is per account and per session, and a
# hub configured with a different one refuses every event as a bad signature.
# Stripe then retries for days, so the symptom is subscriptions that seem to
# apply at random rather than anything that looks like a credential problem.
set -uo pipefail

PORT="${1:-8180}"
TARGET="localhost:${PORT}/billing/webhook"
ENV_FILE="${2:-.env.hub}"

idle() {
    # A lane that is doing nothing, staying alive so the others can run.
    printf '%s\n' "$1"
    printf 'the rest of the stack is unaffected; billing events are simply not being forwarded.\n'
    while true; do sleep 3600; done
}

command -v stripe >/dev/null 2>&1 || idle \
    "stripe CLI not installed, so no webhooks will reach the hub.
  https://docs.stripe.com/stripe-cli  then  stripe login"

if ! stripe config --list >/dev/null 2>&1; then
    idle "stripe CLI is installed but not logged in: run  stripe login"
fi

SECRET="$(timeout 30 stripe listen --print-secret 2>/dev/null | tr -d '[:space:]')"
if [ -z "$SECRET" ]; then
    idle "could not read a webhook signing secret from the Stripe CLI."
fi

if [ -f "$ENV_FILE" ]; then
    CONFIGURED="$(grep -E '^HUB_STRIPE_WEBHOOK_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
    if [ -z "$CONFIGURED" ]; then
        printf 'note: %s has no HUB_STRIPE_WEBHOOK_SECRET.\n' "$ENV_FILE"
        printf '      the hub answers 503 on every billing route until it does. set it to:\n      %s\n' "$SECRET"
    elif [ "$CONFIGURED" != "$SECRET" ]; then
        # Loud, because nothing downstream says anything at all. The hub refuses
        # each event as a bad signature and Stripe keeps retrying quietly.
        printf '\n*** WEBHOOK SECRET MISMATCH ***\n'
        printf '  %s has a different secret from the one this session will sign with.\n' "$ENV_FILE"
        printf '  every event will be refused as a bad signature, and Stripe will retry for days.\n'
        printf '  set HUB_STRIPE_WEBHOOK_SECRET to:\n    %s\n' "$SECRET"
        printf '  then restart the hub.\n\n'
    fi
fi

printf 'forwarding Stripe events to %s\n' "$TARGET"
# `--latest` rather than the account default: the default can be years behind
# what the code is written against, and testing on the newest payload shape is
# what catches a field Stripe has moved.
exec stripe listen --latest --forward-to "$TARGET"
