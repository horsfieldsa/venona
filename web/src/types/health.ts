/**
 * Aliases onto `src/api/schema.d.ts`, which `make types` generates from the
 * real pydantic models and `make lint-generated` holds to the tree.
 *
 * These were hand-written for the life of the project with nothing checking
 * them, and they had drifted: `ModelHealth` was missing `circuit` entirely --
 * the breaker state `useServiceHealth` reads -- and several fields were typed as
 * required that the model makes optional. A type that says a field is always
 * there does not make it always there; it just moves the failure to runtime.
 *
 * The names are kept even where the generated ones differ (`Health` for
 * `HealthResponse`, `SourceHealth` for `SourcesHealth`), because they are what
 * the components read and renaming them is churn, not migration. Where a name
 * IS the generated one, this file is a re-export and nothing more.
 *
 * See contract C9 on generated artifacts.
 */

import type { components } from '@/api/schema'

type Schemas = components['schemas']

export type Neo4jHealth = Schemas['Neo4jHealth']

/** Gains `circuit` over the hand-written version: the LLM breaker's state,
 *  which `/api/health` has served since the breaker shipped. */
export type ModelHealth = Schemas['ModelHealth']

/** A source that has stopped fetching. Auto-disabling is silent by design --
 *  the source just stops appearing in results -- so this is the only place
 *  "why did the brief get thin?" has a visible answer. */
export type DisabledSource = Schemas['DisabledSource']

export type StrikingSource = Schemas['StrikingSource']

export type SourceHealth = Schemas['SourcesHealth']

/** The daily schedule and when it will actually fire. `enabled: false` with a
 *  non-null `next_run_at` is a real state: the in-process job is off and an
 *  external cron drives the trigger endpoint, so the timestamp is the yaml
 *  cron's stated cadence. */
export type ScheduleHealth = Schemas['ScheduleHealth']

/**
 * `active_run` is the RESEARCH-lane run and only that one. `useRunStream`
 * follows it, and the pipeline is the only run worth watching stage by stage --
 * pointing it at a 20-second lookup would yank the Runs page off whatever
 * someone was reading. Light runs are in `runs`, which is contract C11:
 * everything in flight, so a light run can be shown resolving and "queued
 * behind a full lane" is distinguishable from "running".
 */
export type Health = Schemas['HealthResponse']
