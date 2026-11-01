-- Aliases nobody wrote, and one a person withdrew.
--
-- `acronym_variants` output is written to `aliases` at the tier reserved for
-- all-caps acronyms, so a generated form annotates arbitrary prose exactly as a
-- form a publication actually prints does. Two classes of that output were
-- never right, and both were being shown to readers:
--
--   a person's initials    `Adm. Brad Cooper` generates ABC, `Bassam al-Hassan`
--                          generates BAH. Nobody writes a person that way, and
--                          both collide with real acronyms -- BAH with Booz
--                          Allen Hamilton. The ontology now declares which of
--                          its types are abbreviated at all.
--
--   punctuated forms       `"U.S.".isupper()` is true, so the generator reads
--                          it as an already-acronym tail and pastes it in
--                          whole: NU.S. from `U.S. Navy`, SCU.S. from `U.S.
--                          Space Command`, MOSA("MOSA"), N[C2]. No publication
--                          prints any of them, and they were listed on the
--                          entity's own hover card as things it is called.
--
-- The generator stopped producing both, which fixes tomorrow. This is the rows
-- already written. Delete rather than demote: they are derivable, wrong, and a
-- demoted row still reads back as an alias to every surface that lists them.
--
-- Deleted on `entities.type`, not on an id prefix. The prefix is ontology data,
-- editable at runtime and different per domain, and nothing lints a literal in
-- a `.sql` file -- so a prefix here would be a claim no one checks. A domain
-- with no Person type matches nothing, which is the correct answer for it.
DELETE FROM aliases a
 USING entities e
 WHERE a.entity_id = e.id
   AND e.type = 'Person'
   AND a.added_by = 'acronym_gen';

DELETE FROM aliases
 WHERE added_by = 'acronym_gen'
   AND surface ~ '[^A-Z0-9]';

-- General Atomics Aeronautical Systems is a subsidiary, not another name for
-- its parent. `profile/anchors.yaml` declared both `GA-ASI` and the full name
-- as aliases of `company:general-atomics`, which made every GA-ASI mention
-- evidence for the parent; a curator had already ruled the pair `related` on
-- the live corpus, so the declaration and the verdict contradicted each other.
-- The declaration is withdrawn in the same commit. Withdrawing it does not
-- delete anything -- anchor loading is upsert-only -- which is why this is
-- here. See docs/adr/.
DELETE FROM aliases
 WHERE entity_id = 'company:general-atomics'
   AND surface IN ('GA-ASI', 'General Atomics Aeronautical Systems');
