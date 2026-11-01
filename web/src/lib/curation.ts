/**
 * How a merge conflict is described to the curator.
 *
 * These are DISPLAY strings. The stored `kind` -- `duplicate`, `collision`,
 * `containment`, `resolver_error`, `anchor` -- stays exactly as it is on the
 * wire and in `alias_conflicts`, because it is a contract and because
 * `curate.py` classifies against it. Nothing here may be sent back to the API.
 *
 * They live in one module because the queue's filter chips and the review
 * header both name the same thing, and two copies of a vocabulary drift into
 * two vocabularies.
 */

/**
 * The chip.
 *
 * NOT the stored value. `containment` is this pipeline's word for "one name is
 * a substring of the other", and a curator who has not read `curate.py` has no
 * way to know that -- it names the classifier's internal category rather than
 * the thing on screen.
 */
/**
 * Entity status, in the vocabulary this screen already uses.
 *
 * `provisional` is the database's word and it says the wrong thing to a curator:
 * it sounds like a draft, or like something Venona is unsure it got right. The
 * meaning is narrow and mechanical -- ONE source has mentioned this entity, so
 * it is not projected into the graph yet, and a second sighting promotes it with
 * no judgement from anybody. `one source only` states exactly that, in the words
 * the banner above the queue uses when it asks for a second source. `dormant`
 * has the same problem in miniature: it names a lifecycle state, where the fact
 * a reader can use is that the name has left Venona's reading.
 *
 * Display only. `status` is a wire field the graph projector reads; renaming it
 * there would be a migration for a caption.
 */
export const STATUS_LABEL: Record<string, string> = {
  provisional: 'one source only',
  dormant: "no longer in Venona's reading",
}

/** A status for the screen: mapped when the map knows it, opened up when not. */
export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status.replaceAll('_', ' ')
}

export const STATUS_TONE: Record<string, 'ok' | 'high' | 'neutral'> = {
  active: 'ok',
  provisional: 'high',
  dormant: 'neutral',
  merged: 'neutral',
}

/** Tone per kind, for the badge that names it. Shared: `CurationView` draws the
 *  badge on the filter row and `MergeHistory` names the same kinds. */
export const KIND_TONE: Record<string, 'ok' | 'high' | 'critical' | 'info' | 'neutral'> = {
  duplicate: 'ok',
  collision: 'high',
  containment: 'info',
  resolver_error: 'critical',
  anchor: 'info',
  cross_type: 'high',
  type_conflict: 'ok',
}

/**
 * SHORT ENOUGH FOR THE FILTER ROW, which is the narrow place these appear.
 * Six of them sit side by side above the merge queue in a pane the admin rail
 * has already taken 256px from, and a sentence-length label there costs the
 * set more width than it has: the full phrasings ran to 1382px against a
 * 605px pane, so most of the row scrolled out of sight. Each label is the
 * shortest phrase that still tells one kind from the others; `KIND_HELP`
 * carries the meaning, and it reaches the reader as the `title` on both the
 * chip and the filter segment.
 */
export const KIND_LABEL: Record<string, string> = {
  duplicate: 'two spellings',
  collision: 'shared acronym',
  containment: 'name inside a name',
  resolver_error: 'wrong term',
  anchor: 'placement',
  cross_type: 'same name',
  type_conflict: 'two kinds',
  // `kind` is NULLABLE, and 45 of the open rows are null -- conflicts raised
  // before the classifier existed. On a kind-filtered tab you never meet one,
  // because filtering by kind excludes them by construction; on `all` they are
  // a third of the queue and they arrived with no label and no help at all,
  // so the reason line above the cards simply vanished on those pairs.
  // Keyed on '' because `kindLabel` coalesces null to ''.
  '': 'unclassified',
}

/**
 * What the machine noticed, what it usually means, and WHICH ACTION -- named
 * with the same words as the buttons.
 *
 * Describing the category and stopping ("One name qualifies the other. Often a
 * real parent/subsidiary distinction -- read it.") tells a curator what they are
 * looking at but not what to do with it, and leaves the asymmetry of the mistake
 * unstated. The three actions are not equally
 * recoverable: a merge is reversible through `entity_mappings`, but until it is
 * reversed every assertion about one side reads as an assertion about the
 * other, so a wrong Merge is quietly wrong across the whole graph while a wrong
 * Keep apart is a duplicate that simply comes back on the next run.
 */
export const KIND_HELP: Record<string, string> = {
  duplicate:
    'The same thing written two ways: the names differ only in spelling, punctuation, or a trailing acronym. Usually Merge. If they are two real programs that happen to be spelled alike, Keep apart.',
  collision:
    'Two different things that generate the same acronym. Usually Keep apart: merging these is the expensive mistake, because every claim about one silently becomes a claim about the other.',
  containment:
    'One name contains the other: "DIU CAMP XLUUV" and "XLUUV". That is either one thing written long and short, or a parent and its child, and the names alone cannot tell you which. Read the evidence: Merge if it is one thing, relate them as part of if the longer one sits under the shorter, Keep apart if they are unrelated.',
  resolver_error:
    'Venona produced a term that neither name supports. Not a judgement call: Venona got this wrong. Skip it.',
  anchor:
    'A proposed place in the hierarchy for a name that connects to nothing. Choose part of to accept the placement, or Keep apart. Never Merge: an anchor is a parent, not the same thing.',
  cross_type:
    'One name held by two different kinds of thing, such as a program and the aircraft it delivers. Venona keeps those apart on purpose, so there is no Merge here. Relate them if one belongs to the other, or Keep apart and rename one of them so a reader can tell which is which.',
  type_conflict:
    'One thing that got filed under two different kinds, because these two kinds are easy to read for each other. Pick the kind it really is: both sides fold into a single entry under that kind, and every mention of either keeps working. If they turn out to be two real things, Keep apart instead.',
  // The null-kind rows. They predate the classifier, so nothing recorded WHY
  // the pair was raised — which is exactly the thing this line normally says.
  // Better to state that than to leave the row blank and let the curator assume
  // the app forgot to render something.
  '':
    'Raised before Venona classified conflicts, so there is no recorded reason for this pair; read both sides on their own terms. The same three actions apply, and the same asymmetry: a wrong Merge is quietly wrong across the whole graph, a wrong Keep apart just comes back next run.',
}

/**
 * The model's verdict enum (`merge` / `part_of` / `distinct`), in the words the
 * BUTTONS use. "suggests distinct" left the reader to work out that the
 * matching control is called "Keep apart".
 */
export const SUGGESTION_LABEL: Record<string, string> = {
  merge: 'Merge',
  part_of: 'part of',
  distinct: 'Keep apart',
}

/** A suggestion for the screen, opened up when the map has never met it. */
export function suggestionLabel(value: string): string {
  return SUGGESTION_LABEL[value] ?? value.replaceAll('_', ' ').toLowerCase()
}

/** Re-exported so existing callers keep one import. `lib/predicate.ts` owns it. */
export { predicateLabel } from '@/lib/predicate'

/**
 * Why two names became one, in the reader's words.
 *
 * `entity_mappings.reason` is written by the machinery that did the merging and
 * reads like it: `curated: containment`, `auto: generic tail`, `auto: person
 * form`. Those are this repo's words for its own rules, and nobody outside it
 * has met them -- "curated containment" was the first thing an operator caught
 * on the name page, which is exactly the surface DESIGN.md means when it says
 * an admin is a user too.
 *
 * The suffix reuses `KIND_LABEL` wherever it overlaps, so the merge queue and
 * the name page cannot end up with two different phrases for one rule.
 */
const MERGE_RULE: Record<string, string> = {
  // The derivational rules, each named for what a reader can SEE in the pair.
  model: "Venona's reading matched them and the names agreed",
  'declared alias': 'one was already declared another name for the other',
  'same name': 'the same name, spelled two ways',
  'acronym of': "one name is the other's acronym",
  'generic tail': 'the longer name only adds a general word',
  'person form': "the same person's name, written two ways",
  containment: 'one name sits inside the other',
  duplicate: 'two spellings of one thing',
  'merge direction corrected': 'after the direction was corrected',
  // What a reviewer's own verdict records. The rest of this table names the
  // rule that fired; a person merging two names IS the reason, so this says
  // what they did rather than restating who they are.
  merge: 'after reading the evidence on both',
}

/** Who did it. The half of the reason before the colon. */
const MERGE_ACTOR: Record<string, string> = {
  auto: 'Merged by a rule',
  curated: 'Merged by a reviewer',
  manual: 'Merged by hand',
}

export function mergeReasonLabel(reason: string | null | undefined): string {
  const raw = (reason ?? '').trim()
  if (!raw) return 'No reason recorded'
  const [head, ...rest] = raw.split(':')
  const actor = MERGE_ACTOR[head.trim()]
  const rule = rest.join(':').trim()
  // An unknown reason renders as itself rather than as a guess. It is still a
  // machine string, but a wrong translation is worse than an untranslated one.
  if (!actor) return raw
  if (!rule) return actor
  return `${actor}: ${MERGE_RULE[rule] ?? KIND_LABEL[rule] ?? rule}`
}

/**
 * Where a spelling came from, when no article recorded it.
 *
 * `aliases.added_by` is the same kind of string: `acronym_gen` is the generator
 * that made it, not a sentence.
 */
const ALIAS_ORIGIN: Record<string, string> = {
  extraction: 'read in an article',
  acronym_gen: 'generated from the full name',
  acronym_lookup: 'found by looking the acronym up',
  seed: 'set up with this domain',
  manual: 'entered by hand',
}

export function aliasOriginLabel(addedBy: string | null | undefined): string {
  const raw = (addedBy ?? '').trim()
  if (!raw) return 'origin not recorded'
  return ALIAS_ORIGIN[raw] ?? raw.replaceAll('_', ' ')
}

/**
 * The label, or an empty string when there is nothing to say.
 *
 * A conflict with no `kind` gets NO CHIP -- callers skip it on empty. Not the
 * word "unclassified": that reads as a category the classifier assigns, when it
 * is the absence of one, and sends the curator looking for a meaning that does
 * not exist on the one row where the evidence is all they have to go on.
 */
export function kindLabel(kind: string | null | undefined): string {
  return KIND_LABEL[kind ?? ''] ?? (kind ?? '').replaceAll('_', ' ')
}
