/**
 * What a change costs, per document.
 *
 * DO NOT SAY "applies immediately": it reads as "there is a button that applies
 * it", but `POST /rescore` recomputes cluster scores from STORED per-axis scores
 * using the rubric WEIGHTS — it cannot see `relevance_profile.md`, which is
 * prose fed to the model at analyze time. A re-score after editing the profile
 * reports zero changes and looks broken.
 */
export const HINTS: Record<string, string> = {
  free: 'Takes effect the next time Venona reads. Nothing it has already read is re-scored.',
  weights_free:
    'Changing a weight re-ranks what Venona has already read. Changing a description takes effect on the next run.',
  next_run: 'Takes effect on the next run.',
  lexicon:
    'Takes effect on the next run. The non_distinguishing list is not cosmetic: it decides which near-identical names may be treated as the same thing.',
  brief_sections:
    'Takes effect on the next brief. Renaming a section does not rewrite briefs already written: they keep their old headings, so the archive ends up with both.',
  sources:
    'Takes effect on the next run. A source switched off after repeated failures stays off until you switch it back on; editing this file does not revive it.',
  ontology:
    'Checked and applied when you save. Removing a kind of relationship takes those connections out of the graph the next time it is rebuilt. Nothing is lost, and they come back if you add it again.',
  anchors:
    'Saving stores the file and nothing more. Press Apply to put it into the graph.',
  settings:
    'This file belongs to this instance. Most fields apply as soon as you save, and the ones that need a restart say so. Passwords and keys do not belong here: they live on the server, and this document is shown in a browser.',
}

/**
 * The hint for a document, or '' when it declares none.
 *
 * The `title` of the Save button, not a paragraph above the editor: standing
 * explanation between the reader and the file they came to edit is the same
 * sentence every time they open that document. On the button it describes what
 * saving will and will not do, which is the moment the question is asked.
 */
export function hintFor(rescoreHint: string | null | undefined): string {
  return rescoreHint ? (HINTS[rescoreHint] ?? '') : ''
}
