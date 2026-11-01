"""The `hub` CLI. Thin: every subcommand delegates to the same modules the
app uses, so the CLI and the routes can never disagree.

Secret-minting commands PRINT and never write -- the instance Makefile's
`prod-secrets` rule, for the same reason: a command that silently overwrites a
secrets file is one typo away from locking you out of your own machine.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from hub import grants, ids, jwt, seal, security, stripe
from hub.config import get_settings
from hub.db import core as db
from hub.db.migrate import migrate
from hub.repositories import access_requests as requests_repo
from hub.repositories import instances as instances_repo
from hub.repositories import users as users_repo


@contextmanager
def _db() -> Iterator[db.Conn]:
    conn = db.connect(get_settings().database_url)
    try:
        yield conn
    finally:
        conn.close()


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _instance_by_slug(conn: db.Conn, slug: str) -> dict | None:
    row = instances_repo.by_slug(conn, slug)
    if row is None:
        print(f"no instance {slug!r}", file=sys.stderr)
    return row


def _migrate(_args: argparse.Namespace) -> int:
    settings = get_settings()
    if db.ensure_database(settings.database_url):
        print(f"created database {settings.database_url.rsplit('/', 1)[-1]}")
    with _db() as conn:
        applied = migrate(conn)
    print("applied: " + (", ".join(applied) if applied else "nothing to do"))
    return 0


def _secrets(_args: argparse.Namespace) -> int:
    print(f"HUB_SESSION_SECRET={security.new_secret()}")
    print(f"HUB_SEAL_KEY={seal.new_seal_key()}")
    print(f"HUB_ADMIN_TOKEN={security.new_admin_token()}")
    return 0


def _keys(args: argparse.Namespace) -> int:
    if args.keys_command == "new":
        encoded = jwt.generate_signing_key()
        print(f"HUB_OIDC_SIGNING_KEY={encoded}")
        print(f"# kid: {jwt.kid_for(jwt.load_signing_key(encoded))}", file=sys.stderr)
        return 0
    return 2


def _instances(args: argparse.Namespace) -> int:
    settings = get_settings()
    with _db() as conn:
        match args.instances_command:
            case "add":
                if not security.looks_like_mgmt_token(args.mgmt_token):
                    return _fail(
                        "--mgmt-token must be a vmgmt_ value minted on the instance "
                        "with `venona auth mgmt-token`"
                    )
                if instances_repo.by_slug(conn, args.slug) is not None:
                    return _fail(f"slug {args.slug!r} is already registered")
                try:
                    public_url = security.normalized_public_url(args.public_url)
                except ValueError as exc:
                    return _fail(str(exc))
                client_secret = security.new_secret()
                client_id = ids.client_id()
                instances_repo.create(
                    conn,
                    instance_id=ids.instance_id(),
                    slug=args.slug,
                    display_name=args.name,
                    tagline=args.tagline,
                    public_url=public_url,
                    oidc_client_id=client_id,
                    oidc_client_secret_hash=security.hash_token(client_secret),
                    mgmt_token_sealed=seal.seal(settings.seal_key, args.mgmt_token),
                )
                print(f"VENONA_OIDC_ISSUER={settings.public_url}")
                print(f"VENONA_OIDC_CLIENT_ID={client_id}")
                # Shown exactly once; only the hash survives.
                print(f"VENONA_OIDC_CLIENT_SECRET={client_secret}")
            case "ls":
                for row in instances_repo.list_all(conn):
                    seen = row["last_seen_at"].isoformat() if row["last_seen_at"] else "never"
                    print(
                        f"{row['slug']:<20} {row['status']:<12} {row['public_url']:<40} seen {seen}"
                    )
            case "show":
                row = _instance_by_slug(conn, args.slug)
                if row is None:
                    return 1
                for key in (
                    "id",
                    "slug",
                    "display_name",
                    "tagline",
                    "public_url",
                    "status",
                    "oidc_client_id",
                    "last_seen_at",
                    "poll_misses",
                    "created_at",
                ):
                    print(f"{key}: {row[key]}")
                status = db.jdump(row["last_status"]) if row["last_status"] else None
                print(f"last_status: {status}")
            case "retire" | "reinstate":
                row = _instance_by_slug(conn, args.slug)
                if row is None:
                    return 1
                status = "retired" if args.instances_command == "retire" else "provisioning"
                instances_repo.set_fields(conn, row["id"], status=status)
                print(f"{args.slug}: {status}")
            case "rotate-secret":
                row = _instance_by_slug(conn, args.slug)
                if row is None:
                    return 1
                client_secret = security.new_secret()
                instances_repo.set_secret_hash(conn, row["id"], security.hash_token(client_secret))
                print(f"VENONA_OIDC_CLIENT_ID={row['oidc_client_id']}")
                print(f"VENONA_OIDC_CLIENT_SECRET={client_secret}")
            case "pricing":
                row = _instance_by_slug(conn, args.slug)
                if row is None:
                    return 1
                if not settings.billing_enabled:
                    return _fail(
                        "billing is not configured on this hub: set HUB_STRIPE_SECRET_KEY "
                        "and HUB_STRIPE_WEBHOOK_SECRET first"
                    )
                # The product is NAMED AFTER THE INSTANCE, because that name is
                # what a reader sees in Stripe's customer portal and on the
                # invoice they expense. Two domains sharing one product are
                # indistinguishable in both places.
                product = asyncio.run(
                    stripe.create_product(
                        settings.stripe_secret_key,
                        name=f"Venona {row['display_name']}",
                        description=row["tagline"] or f"Venona, {row['display_name']}",
                        instance_id=row["id"],
                        slug=row["slug"],
                    )
                )
                prices = {}
                for interval, amount in (("month", args.monthly), ("year", args.annual)):
                    prices[interval] = asyncio.run(
                        stripe.create_price(
                            settings.stripe_secret_key,
                            product_id=product["id"],
                            unit_amount=amount,
                            interval=interval,
                            currency=args.currency,
                            nickname=f"{row['display_name']} {interval}ly",
                            instance_id=row["id"],
                            slug=row["slug"],
                        )
                    )["id"]
                instances_repo.set_pricing(
                    conn,
                    slug=row["slug"],
                    product_id=product["id"],
                    price_monthly=prices["month"],
                    price_annual=prices["year"],
                    # The amounts as typed, so the gallery can name a price
                    # before any subscription exists to read one off.
                    monthly_amount=args.monthly,
                    annual_amount=args.annual,
                    currency=args.currency,
                )
                print(f"{row['slug']}: {product['id']}")
                print(f"  monthly {prices['month']}  {args.monthly / 100:.2f} {args.currency}")
                print(f"  annual  {prices['year']}  {args.annual / 100:.2f} {args.currency}")
            case "set-mgmt-token":
                row = _instance_by_slug(conn, args.slug)
                if row is None:
                    return 1
                if not security.looks_like_mgmt_token(args.mgmt_token):
                    return _fail("--mgmt-token must be a vmgmt_ value")
                instances_repo.set_fields(
                    conn, row["id"], mgmt_token_sealed=seal.seal(settings.seal_key, args.mgmt_token)
                )
                print(f"{args.slug}: mgmt token resealed")
            case "describe":
                # The two strings a stranger picks a domain on. Settable after
                # registration because the gallery renders both, and until now
                # they could only be chosen at `add` and never corrected.
                row = _instance_by_slug(conn, args.slug)
                if row is None:
                    return 1
                instances_repo.set_fields(
                    conn,
                    row["id"],
                    display_name=args.name or None,
                    tagline=args.tagline if args.tagline is not None else None,
                )
                fresh = _instance_by_slug(conn, args.slug)
                print(f"{args.slug}: {fresh['display_name']}")
                print(f"  {fresh['tagline'] or '(no tagline)'}")
            case "set-url":
                row = _instance_by_slug(conn, args.slug)
                if row is None:
                    return 1
                try:
                    public_url = security.normalized_public_url(args.public_url)
                except ValueError as exc:
                    return _fail(str(exc))
                instances_repo.set_fields(conn, row["id"], public_url=public_url)
                print(f"{args.slug}: {public_url}")
    return 0


def _users(args: argparse.Namespace) -> int:
    with _db() as conn:
        match args.users_command:
            case "add":
                email = args.email.strip().lower()
                if users_repo.by_email(conn, email) is not None:
                    return _fail(f"{email} already exists")
                users_repo.create(conn, user_id=ids.user_id(), email=email, display_name=args.name)
                print(f"created {email}")
            case "ls":
                for row in users_repo.list_all(conn):
                    login = row["last_login_at"].isoformat() if row["last_login_at"] else "never"
                    print(f"{row['email']:<40} {row['status']:<10} last login {login}")
            case "suspend" | "reinstate":
                row = users_repo.by_email(conn, args.email)
                if row is None:
                    return _fail(f"no user {args.email!r}")
                status = "suspended" if args.users_command == "suspend" else "active"
                users_repo.set_status(conn, row["id"], status)
                print(f"{row['email']}: {status}")
    return 0


def _grant(args: argparse.Namespace) -> int:
    with _db() as conn:
        user = users_repo.by_email(conn, args.email)
        if user is None:
            return _fail(f"no user {args.email!r} -- `hub users add` first")
        instance = _instance_by_slug(conn, args.slug)
        if instance is None:
            return 1
        users_repo.grant(conn, user_id=user["id"], instance_id=instance["id"], role=args.role)
        print(f"{args.email} -> {args.slug} as {args.role}")
    return 0


def _revoke(args: argparse.Namespace) -> int:
    with _db() as conn:
        user = users_repo.by_email(conn, args.email)
        instance = instances_repo.by_slug(conn, args.slug)
        if user is None or instance is None:
            return _fail("no such user or instance")
        users_repo.revoke(conn, user_id=user["id"], instance_id=instance["id"])
        print(f"{args.email} -x- {args.slug}")
    return 0


def _memberships(args: argparse.Namespace) -> int:
    with _db() as conn:
        if args.slug:
            instance = _instance_by_slug(conn, args.slug)
            if instance is None:
                return 1
            for row in users_repo.members_of(conn, instance["id"]):
                print(f"{row['email']:<40} {row['role']:<8} {row['status']}")
        else:
            for user in users_repo.list_all(conn):
                for row in users_repo.instances_for(conn, user["id"]):
                    print(f"{user['email']:<40} {row['slug']:<20} {row['role']}")
    return 0


def _requests(args: argparse.Namespace) -> int:
    with _db() as conn:
        match args.requests_command:
            case "ls":
                for row in requests_repo.list_pending(conn, kind=args.kind):
                    wanted = row["wanted_slug"] or "any"
                    note = (row["note"] or "").replace("\n", " ")[:60]
                    print(
                        f"{row['id']}  {row['kind']:<7} {row['email']:<40} "
                        f"wants {wanted:<20} {note}"
                    )
            case "grant":
                row = requests_repo.by_id(conn, args.id)
                if row is None:
                    return _fail(f"no request {args.id!r}")
                # Same refusal as the operator page's, and it has to be in both:
                # this one takes an id off a listing that shows launch rows too.
                if row["kind"] == "launch":
                    return _fail(
                        f"{args.id} left an address at launch and named no domain. "
                        "Granting one picks a domain on their behalf; wait for them "
                        "to ask for a domain, which raises an access request."
                    )
                instance = _instance_by_slug(conn, args.slug)
                if instance is None:
                    return 1
                grants.grant_access(
                    conn, email=row["email"], instance_id=instance["id"], role=args.role
                )
                requests_repo.set_status(conn, args.id, "granted")
                print(f"{row['email']} -> {args.slug} as {args.role}")
            case "dismiss":
                if requests_repo.by_id(conn, args.id) is None:
                    return _fail(f"no request {args.id!r}")
                requests_repo.set_status(conn, args.id, "dismissed")
                print(f"{args.id}: dismissed")
    return 0


def _poll(args: argparse.Namespace) -> int:
    import asyncio

    from hub.poller import poll_once

    if not args.once:
        return _fail("only --once is supported; the serving app runs the loop")
    results = asyncio.run(poll_once(get_settings()))
    if not results:
        print("nothing to poll (no non-retired instances)")
    for slug, status in sorted(results.items()):
        print(f"{slug:<20} {status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hub")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="create the database if needed, apply pending migrations")
    sub.add_parser("secrets", help="generate hub secrets (prints; writes nothing)")

    keys_parser = sub.add_parser("keys", help="the RS256 signing key")
    keys_sub = keys_parser.add_subparsers(dest="keys_command", required=True)
    keys_sub.add_parser("new", help="a HUB_OIDC_SIGNING_KEY (prints; writes nothing)")

    inst = sub.add_parser("instances", help="the directory")
    inst_sub = inst.add_subparsers(dest="instances_command", required=True)
    add = inst_sub.add_parser("add", help="register an instance, mint its OIDC client")
    add.add_argument("--slug", required=True)
    add.add_argument("--name", required=True, help="display name")
    add.add_argument("--public-url", required=True)
    add.add_argument("--tagline", default="")
    add.add_argument("--mgmt-token", required=True, help="from `venona auth mgmt-token`")
    inst_sub.add_parser("ls", help="every instance, with status")
    for name, help_text in (
        ("show", "one instance, in full"),
        ("retire", "drop from listings and the poll cycle"),
        ("reinstate", "back to provisioning; the poller takes it from there"),
        ("rotate-secret", "new OIDC client secret (prints once)"),
    ):
        p = inst_sub.add_parser(name, help=help_text)
        p.add_argument("slug")
    p = inst_sub.add_parser("pricing", help="create this instance's Stripe product and prices")
    p.add_argument("slug")
    p.add_argument("--monthly", type=int, required=True, help="cents, e.g. 1200")
    p.add_argument("--annual", type=int, required=True, help="cents, e.g. 12000")
    p.add_argument("--currency", default="usd")

    p = inst_sub.add_parser("set-mgmt-token", help="reseal a rotated instance token")
    p.add_argument("slug")
    p.add_argument("--mgmt-token", required=True)
    p = inst_sub.add_parser("describe", help="the name and tagline a reader chooses on")
    p.add_argument("slug")
    p.add_argument("--name", default="", help="the display name")
    p.add_argument("--tagline", help="one line under the name; pass an empty string to clear")
    p = inst_sub.add_parser("set-url", help="the instance moved")
    p.add_argument("slug")
    p.add_argument("--public-url", required=True)

    users = sub.add_parser("users", help="identities")
    users_sub = users.add_subparsers(dest="users_command", required=True)
    p = users_sub.add_parser("add", help="provision a user (they sign in with Google later)")
    p.add_argument("email")
    p.add_argument("--name", default=None)
    users_sub.add_parser("ls", help="every user")
    for name in ("suspend", "reinstate"):
        p = users_sub.add_parser(name)
        p.add_argument("email")

    p = sub.add_parser("grant", help="membership: user -> instance with a role")
    p.add_argument("email")
    p.add_argument("slug")
    p.add_argument("role", choices=("admin", "reader"))
    p = sub.add_parser("revoke", help="remove a membership")
    p.add_argument("email")
    p.add_argument("slug")
    p = sub.add_parser("memberships", help="list memberships, optionally for one instance")
    p.add_argument("slug", nargs="?", default=None)

    reqs = sub.add_parser("requests", help="access-request triage")
    reqs_sub = reqs.add_subparsers(dest="requests_command", required=True)
    reqs_ls = reqs_sub.add_parser("ls", help="pending requests")
    # Both doors by default: the queue is small and an operator asking what is
    # waiting means everything, not one bucket of it.
    reqs_ls.add_argument("--kind", choices=requests_repo.KINDS, default=None, help="only this kind")
    p = reqs_sub.add_parser("grant", help="user (created if new) + membership + mark granted")
    p.add_argument("id")
    p.add_argument("slug")
    p.add_argument("role", choices=("admin", "reader"))
    p = reqs_sub.add_parser("dismiss")
    p.add_argument("id")

    poll = sub.add_parser("poll", help="poll the mgmt surfaces now")
    poll.add_argument("--once", action="store_true", help="one cycle, then exit")

    args = parser.parse_args(argv)
    handlers = {
        "migrate": _migrate,
        "secrets": _secrets,
        "keys": _keys,
        "instances": _instances,
        "users": _users,
        "grant": _grant,
        "revoke": _revoke,
        "memberships": _memberships,
        "requests": _requests,
        "poll": _poll,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
