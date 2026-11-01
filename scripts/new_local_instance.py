"""Scaffold a second local venona instance beside the default one.

`make new-local-instance SLUG=<slug>` allocates the next +10 port block,
writes `local/<slug>.env` (which `make dev INSTANCE=<slug>` includes and
exports), and lays down a per-instance `config`/`profile` pair from
`fixtures/skeleton/` -- blank, loader-valid documents the wizard's drafts
replace -- plus a `domain.yaml` carrying the PLACEHOLDER slug, so the new
stack boots straight into the setup wizard (`setup_service.is_configured`).
docs/adr/0143. An earlier version copied the repo's DEFENSE documents as
"placeholders", which made an unconfigured instance impersonate the defense
corpus in the editor and in any run that fired pre-setup;
tests/test_instance_skeleton.py now pins each skeleton to its real loader.

Ports, not compose magic: each instance's stack is its own compose project
(`-p venona-<slug>`), which scopes containers and the postgres volume; the
env file is the single place its ports and paths are stated.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "local"

#: The default instance's ports; each scaffolded instance takes base + 10*n.
BASE = {
    "API_PORT": 8000,
    "WEB_PORT": 5173,
    "VENONA_PG_PORT": 5432,
    "VENONA_NEO4J_HTTP_PORT": 7474,
    "VENONA_NEO4J_BOLT_PORT": 7687,
}


def main() -> int:
    sys.path.insert(0, str(REPO / "backend"))
    from venona.services.instance_skeleton import RESERVED_SLUGS, SLUG_PATTERN, seed

    if (
        len(sys.argv) != 2
        or not SLUG_PATTERN.fullmatch(sys.argv[1])
        or sys.argv[1] in RESERVED_SLUGS
    ):
        print(
            f"usage: new_local_instance.py <slug matching {SLUG_PATTERN.pattern}>", file=sys.stderr
        )
        return 2
    slug = sys.argv[1]
    env_path = LOCAL / f"{slug}.env"
    if env_path.exists():
        print(f"{env_path} already exists -- refusing to overwrite", file=sys.stderr)
        return 2

    LOCAL.mkdir(exist_ok=True)
    offset = 10 * (1 + len(list(LOCAL.glob("*.env"))))

    root = LOCAL / slug
    config_dir = root / "config"
    profile_dir = root / "profile"
    data_dir = root / "data"
    for directory in (config_dir, profile_dir, data_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # Blank, loader-valid skeletons the wizard's drafts replace -- see
    # services/instance_skeleton.py for why never the repo's own documents.
    seed(
        config_dir,
        profile_dir,
        skeleton_dir=REPO / "fixtures" / "skeleton",
        settings_src=REPO / "config" / "settings.yaml",
    )

    ports = {key: value + offset for key, value in BASE.items()}
    env_path.write_text(
        "# Instance env for `make dev INSTANCE={slug}`. Included and exported by\n"
        "# the Makefile; every path and port this stack differs on is HERE.\n"
        "API_PORT={API_PORT}\n"
        "WEB_PORT={WEB_PORT}\n"
        "VENONA_PG_PORT={VENONA_PG_PORT}\n"
        "VENONA_NEO4J_HTTP_PORT={VENONA_NEO4J_HTTP_PORT}\n"
        "VENONA_NEO4J_BOLT_PORT={VENONA_NEO4J_BOLT_PORT}\n"
        "VENONA_NEO4J_DIR=neo4j-{slug}\n"
        "VENONA_SLUG={slug}\n"
        "VENONA_DATABASE_URL=postgresql://venona:venona-local@127.0.0.1:{VENONA_PG_PORT}/venona\n"
        "NEO4J_URI=bolt://127.0.0.1:{VENONA_NEO4J_BOLT_PORT}\n"
        "VENONA_CONFIG_DIR={config_dir}\n"
        "VENONA_PROFILE_DIR={profile_dir}\n"
        "VENONA_DATA_DIR={data_dir}\n".format(
            slug=slug,
            config_dir=config_dir,
            profile_dir=profile_dir,
            data_dir=data_dir,
            **ports,
        )
    )

    print(f"scaffolded instance {slug!r}:")
    print(f"  env      {env_path}")
    print(
        f"  ports    api :{ports['API_PORT']}  web :{ports['WEB_PORT']}  pg :{ports['VENONA_PG_PORT']}  neo4j :{ports['VENONA_NEO4J_HTTP_PORT']}/{ports['VENONA_NEO4J_BOLT_PORT']}"
    )
    print(f"  config   {config_dir}")
    print()
    print(f"next:  make dev INSTANCE={slug}")
    print(f"then visit http://127.0.0.1:{ports['WEB_PORT']}/setup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
