/* The graph's timers must not start under `prefers-reduced-motion: reduce`;
 * the stylesheet only stops the CSS half. */
export const reducedMotion = (): boolean =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches
