import { ref, toValue, watchEffect, type MaybeRefOrGetter } from 'vue'

/**
 * Fire `onReach` while a sentinel element is in (or within 200px of) view,
 * pausing whenever `disabled` says so.
 *
 * The root is always passed explicitly, because `window` never scrolls in this
 * app — `AppShell`'s content region is `overflow: hidden`, so the only real
 * scrollers are `ViewShell`'s exposed `scroller` (`scroll="page"` views) or a
 * view's own inner scroll box (`scroll="none"` views).
 *
 * Two reactive layers rather than firing from the observer callback directly:
 * the observer only reports edge crossings, so a page that loads without
 * pushing the sentinel out of view would never re-trigger it. Tracking
 * `intersecting` as state and firing from a watcher means `disabled` releasing
 * (loading finished) re-fires while the sentinel is still visible — which is
 * exactly the "one page was not enough to fill the viewport" case.
 *
 * Consumers keep a visible "Load more" button too: it is the keyboard and
 * screen-reader path, and the whole path when IntersectionObserver is absent.
 */
export function useInfiniteScroll(
  root: MaybeRefOrGetter<HTMLElement | null | undefined>,
  sentinel: MaybeRefOrGetter<HTMLElement | null | undefined>,
  onReach: () => void,
  options: { disabled?: () => boolean } = {},
) {
  const intersecting = ref(false)

  watchEffect((onCleanup) => {
    if (typeof IntersectionObserver === 'undefined') return
    const el = toValue(sentinel)
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        intersecting.value = entries.some((entry) => entry.isIntersecting)
      },
      { root: toValue(root) ?? null, rootMargin: '200px 0px' },
    )
    observer.observe(el)
    onCleanup(() => {
      observer.disconnect()
      intersecting.value = false
    })
  })

  watchEffect(() => {
    if (!intersecting.value) return
    if (options.disabled?.()) return
    onReach()
  })

  return { intersecting }
}
