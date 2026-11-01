import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiGet, apiPost, errorText } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import type { LearnQuestion, LearnQuestionPage } from '@/types/learning'

const PAGE = 50

/**
 * The proposed-question review queue (admin). Verdicts are optimistic — the
 * row leaves the list at once, the queue-shape convention — and a failed
 * write puts it back with the error named.
 */
export const useLearnQuestionsStore = defineStore('learnQuestions', () => {
  const items = ref<LearnQuestion[]>([])
  const total = ref(0)
  const loaded = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    loaded.value = true
    loading.value = true
    error.value = null
    try {
      const page = await apiGet<LearnQuestionPage>(`/learning/questions?limit=${PAGE}&offset=0`)
      items.value = page.items
      total.value = page.total
    } catch (err) {
      error.value = errorText(err, 'could not load the question queue')
      loaded.value = false
    } finally {
      loading.value = false
    }
  }

  async function decide(question: LearnQuestion, action: 'approve' | 'reject'): Promise<void> {
    const index = items.value.findIndex((q) => q.id === question.id)
    if (index < 0) return
    items.value = items.value.filter((q) => q.id !== question.id)
    total.value = Math.max(0, total.value - 1)
    try {
      await apiPost(`/learning/questions/${encodeURIComponent(question.id)}/decide`, { action })
    } catch (err) {
      items.value = [
        ...items.value.slice(0, index),
        question,
        ...items.value.slice(index),
      ]
      total.value += 1
      error.value = errorText(err, 'the verdict did not save')
    }
  }

  // A finished generation run proposes rows; surface them without a manual
  // refresh.
  onFreshness('learning', () => {
    if (loaded.value) void load(true)
  })

  return { items, total, loaded, loading, error, load, decide }
})
