/** Types for `vite-strip-comments.mjs`.
 *
 * The implementation stays `.mjs` because `extension/scripts/finish.mjs` runs it
 * under plain node, with no TypeScript in the path. The plugin shape is declared
 * structurally rather than imported from `vite`: this file is resolved from
 * `tooling/`, which has no `node_modules`, and the three projects that consume it
 * are not all on the same Vite major.
 */

export declare function stripJsComments(code: string): string
export declare function stripHtmlComments(html: string): string
export declare function stripCssComments(css: string): string

export declare function stripComments(): {
  name: string
  apply: 'build'
  configResolved(config: {
    publicDir: string
    root: string
    build: { outDir: string }
  }): void
  transformIndexHtml: {
    order: 'post'
    handler(html: string): string
  }
  closeBundle(): Promise<void>
}

export default stripComments
