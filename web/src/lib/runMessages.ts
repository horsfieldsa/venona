/**
 * A run's messages, split by what each one claims.
 *
 * Each message carries its own `level`, and this is the only place the three
 * are separated. That matters because a single run routinely has all three at
 * once: read from the run's status instead, a surface can only pick ONE
 * heading, and the curation backlog then renders under an amber "Why" beside
 * the thing that actually broke.
 *
 * Same rule as `runOutcome.ts`: one decoder, so two
 * surfaces cannot disagree about the same run.
 */

import type { RunMessage } from '@/types/run'

/**
 * Where a `todo` can be acted on.
 *
 * The server sends a SYMBOL, not a path. Route strings belong to the router:
 * a backend emitting `/curation` would keep emitting it the day that route is
 * renamed, and nothing would fail until a reader followed a dead link.
 */
const DESTINATIONS: Record<string, { to: string; label: string }> = {
  curation: { to: '/admin/curation', label: 'Open curation' },
  sources: { to: '/admin/config/sources', label: 'Open sources' },
  config: { to: '/admin/config', label: 'Open configuration' },
}

export function destinationFor(message: RunMessage): { to: string; label: string } | null {
  return (message.to && DESTINATIONS[message.to]) || null
}

export function atLevel(messages: RunMessage[] | null | undefined, level: string): RunMessage[] {
  return (messages ?? []).filter((m) => m.level === level)
}

/** The degradations, and the only ones that belong under a heading explaining
 *  why a run is not clean. */
export function warningsOf(messages: RunMessage[] | null | undefined): RunMessage[] {
  return atLevel(messages, 'warning')
}

/** Work waiting on a person. Re-fires every run until somebody rules on it. */
export function todosOf(messages: RunMessage[] | null | undefined): RunMessage[] {
  return atLevel(messages, 'todo')
}

/** Observations with nothing to do about them. */
export function notesOf(messages: RunMessage[] | null | undefined): RunMessage[] {
  return atLevel(messages, 'note')
}
