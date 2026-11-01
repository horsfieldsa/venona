---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — Optional model fields are nullable and required, never absent

Every property Venona asks a model for is in the schema's `required` list. A
field the model may not be able to fill is typed to allow `null`, and the model
is told to send `null`. Nothing is made optional by leaving it out.

**"Leave it out" is not portable.** A provider that validates strictly rejects a
schema whose `required` omits a declared property, so the permissive reading is
one vendor's behaviour rather than a property of JSON Schema. Venona runs three
harnesses and expects more.

**Null is one meaning for every consumer.** An absent key and an explicit null
are the same thing only if every reader goes through `.get`, and that is a
convention no linter holds. One shape means the reading code has one case.

**The cost of getting it wrong is silent.** `org_type` is meaningless for
Program, Technology, Platform, Person and Country, and only Company,
GovernmentBody, MilitaryUnit and ResearchOrg declare `org_types` in the
ontology. When the object omitted the property rather than nulling it, all 346
Organizations on the first full run came back with it unset — a field that reads
as "we did not ask" when it means "this does not apply".

**Consequence.** `llm/schemas.py` keeps the load-bearing sentence at the
`org_type` property and cites this record; `publisher`, `published_hint`,
`expansion`, `definition`, `quote` and `choices` are the same shape for the same
reason. A new optional field is `required` plus a nullable type, and its
consumer reads it through `.get`. Contract C10 governs what the enums are
generated from.
