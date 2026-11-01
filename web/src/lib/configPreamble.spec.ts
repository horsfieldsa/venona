import { describe, expect, it } from 'vitest'

import { preambleLines } from './configPreamble'

/** A document long enough that folding its header is worth doing. */
function withBody(header: string[], bodyLines = 60): string {
  const body = Array.from({ length: bodyLines }, (_, i) => `key_${i}: value`)
  return [...header, ...body].join('\n')
}

const HEADER_16 = Array.from({ length: 16 }, (_, i) => `# ontology header line ${i}`)

describe('preambleLines', () => {
  it('folds a long header', () => {
    expect(preambleLines(withBody(HEADER_16), 'yaml')).toBe(16)
  })

  it('folds nothing in a markdown document', () => {
    expect(preambleLines(withBody(HEADER_16), 'markdown')).toBe(0)
  })

  it('folds nothing when the file opens on content', () => {
    expect(preambleLines(withBody(['version: 5']), 'yaml')).toBe(0)
  })

  // The `settings.yaml` shape: a preamble, a blank, then a warning attached to
  // the line under it. Folding through the blank would take the warning away
  // from the line it governs.
  it('stops at the first blank line', () => {
    const doc = withBody([
      '# Non-secret configuration.',
      '# Secrets go in .env.',
      '# Edit them in Admin, Configuration.',
      '',
      '# Managed by Venona. Leave this line as it is.',
      'settings_version: 5',
    ])
    expect(preambleLines(doc, 'yaml')).toBe(3)
  })

  // A fresh instance's skeletons, which the setup wizard opens first.
  it('folds nothing when the header is most of the file', () => {
    const skeleton = ['# Skeleton, replaced by the wizard.', '# Empty by design.', '# See docs.', 'defaults:', '  kind: rss']
    expect(preambleLines(skeleton.join('\n'), 'yaml')).toBe(0)
  })

  it('folds nothing when the whole document is short', () => {
    expect(preambleLines(withBody(HEADER_16, 10), 'yaml')).toBe(0)
  })

  it('folds nothing in a file that is only comments', () => {
    expect(preambleLines(HEADER_16.join('\n'), 'yaml')).toBe(0)
  })

  it('leaves a two-line header alone', () => {
    expect(preambleLines(withBody(['# one', '# two']), 'yaml')).toBe(0)
  })

  it('handles CRLF without counting the carriage return as content', () => {
    const doc = withBody(HEADER_16).split('\n').join('\r\n')
    expect(preambleLines(doc, 'yaml')).toBe(16)
  })

  it('folds nothing when the document opens on a YAML directive', () => {
    expect(preambleLines(withBody(['---', ...HEADER_16]), 'yaml')).toBe(0)
  })
})
