"""The claude CLI pin belongs to the image, not to the instance.

`llm.pinned_version` is in `config_service.FROZEN_FIELDS`: it cannot be changed
over HTTP, because `executor.py` execs what `cli_path` resolves to and a session
cookie must not choose the binary. So there is no operator intent here to
respect, and every instance's copy is simply whichever version was current when
that instance was scaffolded. Production's said 2.1.220 while the container
shipped 2.1.251, which nothing noticed for as long as nothing read the file.

The value is a literal on purpose. Reading `LlmSettings.model_fields` would make
this migration mean something different every time the pin moves, which is the
one thing a numbered historical fact must never do. A test holds the head state
equal to the model default, so the next pin bump fails the build until its own
migration is written.
"""

from venona.services.config_migrations import Migration, SetValue

MIGRATION = Migration(
    version=2,
    description="the claude CLI pin follows the image",
    ops=(SetValue(group="llm", key="pinned_version", to="2.1.251", force=True),),
)
