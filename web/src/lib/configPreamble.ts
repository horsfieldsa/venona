/**
 * How many leading comment lines of a configuration document to fold on open.
 *
 * Every YAML file in `config/` and `profile/` opens with a header explaining
 * what it is and what changing it costs: sixteen lines on `ontology.yaml`,
 * twenty-one on `anchors.yaml`. They are load-bearing and stay in the file --
 * they are what a reader coming to it in an editor or a diff has -- but they
 * are also the first screen of a surface someone opened to change line 400.
 *
 * FOLDED, NEVER SPLIT OUT OF THE BUFFER. Removing the header would renumber
 * every line under it, and three things on that screen count lines over the
 * whole file: the editor's own gutter, `ConfigEditor`'s "first difference at
 * line N", and the `line N, column M` PyYAML puts in a refused save. Folding
 * leaves all three true.
 */

/** Leading comment lines to fold, or 0 for none. */
export function preambleLines(content: string, format: string): number {
  if (format !== 'yaml') return 0

  const lines = content.split('\n')

  // STOPS AT THE FIRST BLANK LINE, not the first line of content.
  // `settings.yaml` is a three-line preamble, a blank, and then three more
  // comment lines that annotate `settings_version` and end "Leave this line as
  // it is." Running to the first content line folds that warning away from the
  // line it governs.
  let n = 0
  while (n < lines.length && lines[n].startsWith('#')) n++
  if (n < 3) return 0

  // Not when the fold IS the file. A fresh instance ships skeletons of five to
  // eight lines with three-line headers, and the setup wizard sends people
  // straight to them -- folding there hides most of what is on screen.
  const body = lines.length - n
  if (body < n || lines.length < 40) return 0

  return n
}
