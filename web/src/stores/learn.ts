import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiGet, apiPost, errorText } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import type {
  DeckCard,
  LearningCounts,
  LearningOverview,
  LearningProgress,
  LearningQueue,
  Rating,
  ReviewResult,
} from '@/types/learning'

/**
 * The learner's own state: the session, the progress read, the memory
 * browser. Everything here is scoped server-side to the signed-in user —
 * this store never sees anyone else's rows.
 *
 * THE SESSION LIVES HERE, not in the view. `AppShell`'s `<KeepAlive>` is
 * deliberately `BriefView,GraphView` only, so view-local session state would
 * die on any navigation mid-study; store residency means tabbing away to
 * check the graph and coming back resumes at the same card. Only the card's
 * transient reveal phase is view-local, and resetting that on return is
 * correct anyway.
 */
export const useLearnStore = defineStore('learn', () => {
  const session = ref<DeckCard[]>([])
  const position = ref(0)
  const sessionLoaded = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)
  /** One line about the last grade that failed to save; the card is already
   *  re-queued at the tail, so this is information, not a decision. */
  const reviewError = ref<string | null>(null)

  const dueCount = ref(0)
  const reviewedToday = ref(0)
  const streak = ref(0)
  const dailyGoal = ref(0)
  const deckRemaining = ref(0)

  const current = computed<DeckCard | null>(() => session.value[position.value] ?? null)
  const finished = computed(() => sessionLoaded.value && position.value >= session.value.length)
  const remaining = computed(() => Math.max(0, session.value.length - position.value))

  async function loadSession(force = false): Promise<void> {
    if (sessionLoaded.value && !force) return
    sessionLoaded.value = true
    loading.value = true
    error.value = null
    try {
      const queue = await apiGet<LearningQueue>('/learning/queue')
      session.value = [...queue.due, ...queue.new]
      position.value = 0
      reviewError.value = null
      dueCount.value = queue.counts.due
      reviewedToday.value = queue.counts.reviewed_today
      streak.value = queue.streak
      dailyGoal.value = queue.daily_goal
      deckRemaining.value = queue.counts.deck_remaining
    } catch (err) {
      error.value = errorText(err, 'could not load the study queue')
      sessionLoaded.value = false
    } finally {
      loading.value = false
    }
  }

  /**
   * "Keep studying": a further full batch past the daily pace. The pace is
   * a default, never a wall — refusing a willing learner teaches nothing.
   */
  async function continueStudying(): Promise<void> {
    loading.value = true
    try {
      const queue = await apiGet<LearningQueue>('/learning/queue?more=true')
      session.value = [...queue.due, ...queue.new]
      position.value = 0
      reviewError.value = null
      deckRemaining.value = queue.counts.deck_remaining
    } catch (err) {
      error.value = errorText(err, 'could not fetch more cards')
    } finally {
      loading.value = false
    }
  }

  /**
   * Optimistic: advance first, persist in the background. Every successful
   * grade is already on the server, so the worst a crash costs is the one
   * in-flight grade — while a blocking round trip per card would put 200ms
   * between every keystroke of a session. A failure re-queues the card at
   * the tail and says so; it does not roll the session back.
   */
  async function submitReview(card: DeckCard, rating: Rating): Promise<void> {
    position.value += 1
    reviewedToday.value += 1
    try {
      await apiPost<ReviewResult>('/learning/review', {
        card_key: card.card_key,
        rating,
      })
      if (reviewedToday.value === 1) streak.value = Math.max(streak.value, 1)
    } catch (err) {
      reviewedToday.value -= 1
      session.value = [...session.value, card]
      reviewError.value = errorText(err, 'a grade did not save. The card will come round again')
    }
  }

  // ── progress ───────────────────────────────────────────────────────

  const progress = ref<LearningProgress | null>(null)
  const progressLoaded = ref(false)
  const progressError = ref<string | null>(null)

  async function loadProgress(force = false): Promise<void> {
    if (progressLoaded.value && !force) return
    progressLoaded.value = true
    progressError.value = null
    try {
      progress.value = await apiGet<LearningProgress>('/learning/progress')
    } catch (err) {
      progressError.value = errorText(err, 'could not load progress')
      progressLoaded.value = false
    }
  }

  // ── the terms overview ─────────────────────────────────────────────

  const overview = ref<LearningOverview | null>(null)
  const overviewLoaded = ref(false)
  const overviewError = ref<string | null>(null)

  async function loadOverview(force = false): Promise<void> {
    if (overviewLoaded.value && !force) return
    overviewLoaded.value = true
    overviewError.value = null
    try {
      overview.value = await apiGet<LearningOverview>('/learning/overview')
    } catch (err) {
      overviewError.value = errorText(err, 'could not load your terms')
      overviewLoaded.value = false
    }
  }

  // ── the nudge (rendered on the brief) ─────────────────────────────

  const counts = ref<LearningCounts | null>(null)
  const countsLoaded = ref(false)

  async function loadCounts(force = false): Promise<void> {
    if (countsLoaded.value && !force) return
    countsLoaded.value = true
    try {
      counts.value = await apiGet<LearningCounts>('/learning/counts')
    } catch {
      // A nudge that cannot count has nothing to say; silence IS the
      // degraded rendering, so no error surfaces anywhere.
      counts.value = null
      countsLoaded.value = false
    }
  }

  /** Cards ready right now — what the brief's nudge announces. */
  const ready = computed(() => (counts.value?.due ?? 0) + (counts.value?.new_available ?? 0))

  // A finished generation run or an approval moved the deck; a grade moved
  // this user's own numbers (possibly in another tab). Refresh what is
  // passive — but never the session someone is mid-way through: a stamp must
  // not yank the card being answered.
  onFreshness('learning', () => {
    if (progressLoaded.value) void loadProgress(true)
    if (overviewLoaded.value) void loadOverview(true)
    if (countsLoaded.value) void loadCounts(true)
    if (sessionLoaded.value && finished.value) void loadSession(true)
  })

  return {
    session,
    position,
    current,
    finished,
    remaining,
    loading,
    error,
    reviewError,
    dueCount,
    reviewedToday,
    streak,
    dailyGoal,
    deckRemaining,
    sessionLoaded,
    loadSession,
    continueStudying,
    submitReview,
    progress,
    progressError,
    loadProgress,
    overview,
    overviewError,
    loadOverview,
    counts,
    ready,
    loadCounts,
  }
})
