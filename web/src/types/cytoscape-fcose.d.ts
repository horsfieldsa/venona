/** cytoscape-fcose ships no types. It is a Cytoscape layout extension
 *  registered via `cytoscape.use()`; the options we pass are validated by the
 *  `fixedNodeConstraint` behaviour test, not by the compiler. */
declare module 'cytoscape-fcose' {
  const extension: cytoscape.Ext
  export default extension
}
