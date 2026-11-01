/** Keep source comments out of the browser.
 *
 * Vite minifies the module graph, and minification drops comments. Two paths
 * skip it: the HTML entry, and `public/`, which is copied byte for byte. So a
 * comment in `index.html` or `public/theme-boot.js` is served verbatim to every
 * visitor, and one in `options.html` ships inside the extension.
 *
 * Self-contained on purpose. `web` is on Vite 8 (rolldown/oxc) while
 * `extension` is on Vite 6 (esbuild), and the two majors expose different
 * transform APIs -- `transformWithOxc` preserves comments outright. This runs on
 * the same terms as `scripts/lint_*.py`: no node_modules, no network.
 */

/** Comments a minifier would keep, and so do we: `/*!` and `//!` mark a licence. */
const LEGAL = /^[!*]/

/** Preceded by one of these, a `/` opens a regex literal rather than dividing. */
const REGEX_PRECEDING = new Set([
  'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void',
  'throw', 'case', 'do', 'else', 'yield', 'await',
])

/** Does the `/` at `i` open a regex literal? Looks back at the last token. */
function opensRegex(code, i) {
  let j = i - 1
  while (j >= 0 && /\s/.test(code[j])) j--
  if (j < 0) return true
  const c = code[j]
  if (/[)\]}]/.test(c)) return false
  if (/[\w$]/.test(c)) {
    let k = j
    while (k >= 0 && /[\w$]/.test(code[k])) k--
    return REGEX_PRECEDING.has(code.slice(k + 1, j + 1))
  }
  return true
}

/** Strip `//` and `/* *\/` comments, leaving strings, templates and regexes be.
 *
 * Removes the comment and any run of blank line it leaves behind, so the output
 * has no ragged gaps where a paragraph stood.
 */
export function stripJsComments(code) {
  let out = ''
  let i = 0
  const n = code.length

  while (i < n) {
    const c = code[i]

    if (c === '"' || c === "'") {
      const start = i++
      while (i < n && code[i] !== c) i += code[i] === '\\' ? 2 : 1
      out += code.slice(start, ++i)
      continue
    }

    if (c === '`') {
      const start = i++
      let depth = 0
      while (i < n) {
        if (code[i] === '\\') { i += 2; continue }
        if (depth === 0 && code[i] === '`') { i++; break }
        if (code[i] === '$' && code[i + 1] === '{') { depth++; i += 2; continue }
        if (depth > 0 && code[i] === '}') { depth--; i++; continue }
        i++
      }
      out += code.slice(start, i)
      continue
    }

    if (c === '/' && code[i + 1] === '/') {
      if (LEGAL.test(code[i + 2] ?? '')) {
        const end = code.indexOf('\n', i)
        out += code.slice(i, end === -1 ? n : end)
        i = end === -1 ? n : end
        continue
      }
      const end = code.indexOf('\n', i)
      i = end === -1 ? n : end + 1
      // The line held nothing but the comment: drop its newline too.
      if (/(^|\n)[ \t]*$/.test(out)) out = out.replace(/[ \t]*$/, '')
      else i--
      continue
    }

    if (c === '/' && code[i + 1] === '*') {
      const end = code.indexOf('*/', i + 2)
      const stop = end === -1 ? n : end + 2
      if (LEGAL.test(code[i + 2] ?? '')) { out += code.slice(i, stop); i = stop; continue }
      i = stop
      if (/(^|\n)[ \t]*$/.test(out)) {
        out = out.replace(/[ \t]*$/, '')
        while (i < n && (code[i] === ' ' || code[i] === '\t')) i++
        if (code[i] === '\n') i++
      }
      continue
    }

    if (c === '/' && opensRegex(code, i)) {
      const start = i++
      let inClass = false
      while (i < n) {
        if (code[i] === '\\') { i += 2; continue }
        if (code[i] === '[') inClass = true
        else if (code[i] === ']') inClass = false
        else if (code[i] === '/' && !inClass) { i++; break }
        else if (code[i] === '\n') break
        i++
      }
      while (i < n && /[a-z]/.test(code[i])) i++
      out += code.slice(start, i)
      continue
    }

    out += c
    i++
  }

  return out.replace(/\n{3,}/g, '\n\n').replace(/^\n+/, '')
}

/** Strip `<!-- -->`, including the licence-free ones above `<html>`. */
export function stripHtmlComments(html) {
  return html
    .replace(/[ \t]*<!--(?!\[if)[\s\S]*?-->[ \t]*\n?/g, '')
    .replace(/\n{3,}/g, '\n\n')
}

/** Strip `/* *\/` from CSS, leaving quoted values alone.
 *
 * For stylesheets that reach the browser without passing through a bundler --
 * `extension/dist/mentions.css` is copied by `scripts/finish.mjs` and injected
 * into every page a reader visits.
 */
export function stripCssComments(css) {
  let out = ''
  let i = 0
  while (i < css.length) {
    const c = css[i]
    if (c === '"' || c === "'") {
      const start = i++
      while (i < css.length && css[i] !== c) i += css[i] === '\\' ? 2 : 1
      out += css.slice(start, ++i)
      continue
    }
    if (c === '/' && css[i + 1] === '*') {
      const end = css.indexOf('*/', i + 2)
      const stop = end === -1 ? css.length : end + 2
      if (LEGAL.test(css[i + 2] ?? '')) { out += css.slice(i, stop); i = stop; continue }
      i = stop
      if (/(^|\n)[ \t]*$/.test(out)) {
        out = out.replace(/[ \t]*$/, '')
        while (i < css.length && (css[i] === ' ' || css[i] === '\t')) i++
        if (css[i] === '\n') i++
      }
      continue
    }
    out += c
    i++
  }
  return out.replace(/\n{3,}/g, '\n\n').replace(/^\n+/, '')
}

/** Files under `publicDir` are copied, never compiled. Collect the JS among them. */
async function publicScripts(fs, path, dir, base = '') {
  const found = []
  let entries
  try { entries = await fs.readdir(dir, { withFileTypes: true }) } catch { return found }
  for (const entry of entries) {
    const rel = base ? `${base}/${entry.name}` : entry.name
    if (entry.isDirectory()) found.push(...(await publicScripts(fs, path, path.join(dir, entry.name), rel)))
    else if (/\.(js|mjs)$/.test(entry.name)) found.push(rel)
  }
  return found
}

/** @returns {import('vite').Plugin} */
export function stripComments() {
  let publicDir = ''
  let outDir = ''
  let root = ''

  return {
    name: 'venona:strip-comments',
    apply: 'build',

    configResolved(config) {
      publicDir = config.publicDir
      outDir = config.build.outDir
      root = config.root
    },

    transformIndexHtml: {
      order: 'post',
      handler: (html) => stripHtmlComments(html),
    },

    // `closeBundle`, not `writeBundle`: Vite copies `publicDir` into `outDir`
    // after the bundle is written, so anything earlier rewrites a file that is
    // about to be overwritten by the pristine copy.
    async closeBundle() {
      if (!publicDir) return
      const fs = await import('node:fs/promises')
      const path = await import('node:path')
      const resolvedOut = path.resolve(root, outDir)

      for (const rel of await publicScripts(fs, path, publicDir)) {
        const target = path.join(resolvedOut, rel)
        let code
        try { code = await fs.readFile(target, 'utf8') } catch { continue }
        const stripped = stripJsComments(code)

        // A stripper that corrupts a render-blocking boot script must not do
        // it quietly. Anything that fails to parse fails the build.
        try {
          new Function(stripped)
        } catch (error) {
          this.error(
            `strip-comments corrupted ${rel}: ${error.message}\n` +
              'The file is unchanged. Report the construct it choked on.',
          )
        }
        if (stripped !== code) await fs.writeFile(target, stripped)
      }
    },
  }
}

export default stripComments
