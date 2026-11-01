"""How corroborated a provisional entity must be before it is asked about.

Additive, and an instance that sets it to 0 keeps the behaviour it has.

The filing net was widened deliberately, on the argument that asking costs a
curator one keystroke. That is true of one row. Measured on the live corpus it
had become 455 open rows, of which 383 named an entity seen fewer than twice and
23 had two active sides -- and half of every human keep-apart on a containment
pair was spent on something that drained on its own afterwards.

On the wire because it is the dial on how much a curator is asked, and the right
number depends on how noisy a domain's naming is.
"""

from venona.services.config_migrations import AddKey, Migration

MIGRATION = Migration(
    version=11,
    description="a provisional entity is corroborated before it is asked about",
    ops=(
        AddKey(
            group="pipeline",
            key="file_duplicates_min_mentions",
            block=(
                "  # How many times a PROVISIONAL entity must have been mentioned before a",
                "  # duplicate conflict is filed about it. 0 files about everything.",
                "  #",
                "  # A surface seen once that never comes back drains on its own, and the",
                "  # question it raised was never worth putting. This DEFERS rather than",
                "  # drops: filing is asked of the whole corpus every run, so a second",
                "  # sighting files it then.",
                "  file_duplicates_min_mentions: 2",
            ),
            value=2,
        ),
    ),
)
