<script setup lang="ts">
import { computed } from 'vue'

import CodeEditor from '@/components/config/CodeEditor.vue'
import Button from '@/components/ui/Button.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { preambleLines } from '@/lib/configPreamble'
import type { ConfigDoc } from '@/types/config'

/**
 * The editing pane: the document's own sentence, whatever the reader has to
 * know about the state of it, and the text.
 *
 * The server validates on save through the same loader startup uses — a rubric
 * whose weights do not sum to 1.0, or a source with an unknown `kind`, is
 * rejected there rather than at the end of a run that has already spent money.
 * Nothing here decides that; see docs/adr/0200 for why the editor is a real one.
 */
const props = defineProps<{
  doc: ConfigDoc
  modelValue: string
  /** On-disk content when the file changed underneath us. */
  conflict: string | null
  /** Validation failure, conflict message, or a restart warning. */
  notice: string | null
  /** The buffer differs from the file. */
  dirty: boolean
  /** ...and it was taken against a version that has since been replaced. */
  diverged: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  takeDisk: []
  discard: []
}>()

/**
 * Where the buffer and the file part company, so a discard is an informed
 * choice rather than a blind one.
 *
 * Cheap enough to compute on every keystroke: these documents are a few
 * thousand characters and the comparison stops at the first differing line.
 */
const difference = computed(() => {
  const mine = props.modelValue.split('\n')
  const disk = props.doc.content.split('\n')
  const at = mine.findIndex((line, i) => line !== disk[i])
  return {
    line: at === -1 ? null : at + 1,
    lines: mine.length - disk.length,
    chars: props.modelValue.length - props.doc.content.length,
  }
})


const chars = computed(() => props.modelValue.length)

/**
 * How much of the header to fold, measured on WHAT IS IN THE EDITOR.
 *
 * Not `doc.content`, which is the text on disk: the buffer can be a kept draft
 * or a restored version whose header is a different length, and folding the
 * disk file's count into it puts real configuration out of sight.
 */
const foldLines = computed(() => preambleLines(props.modelValue, props.doc.format))
</script>

<template>
  <main class="editor flex flex-col gap-3 flex-1 min-h-0">
    <p v-if="doc.readonly" class="hint text-xs text-text-secondary">
      Read-only. Built from
      <span v-for="(source, i) in doc.generated_from ?? []" :key="source">
        <code>{{ source }}</code
        ><span v-if="i < (doc.generated_from?.length ?? 0) - 1">, </span> </span
      >. Edit those to change what appears here.
    </p>

    <StatusNote v-if="notice" kind="error">{{ notice }}</StatusNote>

    <div v-if="conflict !== null" class="conflict flex items-center gap-3 items-baseline">
      <span class="text-xs">
        This file has changed since you opened it. Your version is in the editor.
      </span>
      <Button variant="text" @click="emit('takeDisk')">
        take the version on disk
      </Button>
    </div>

    <!-- A draft taken against a version that has since been replaced. Louder than
         the plain unsaved line below because Save would write this buffer over
         a file it never saw, and because the state is usually not deliberate:
         it survives reloads, so a buffer abandoned weeks ago still marks the
         document unsaved and still looks exactly like work in progress. -->
    <div v-else-if="diverged" class="conflict flex flex-col gap-2">
      <span class="text-xs">
        You started these edits from an older version of the file, and it has changed since
        then. <strong>Saving would overwrite the current file with what is in the editor.</strong>
      </span>
      <span class="flex items-center gap-3 items-baseline">
        <span class="text-xs mono text-text-tertiary">
          <template v-if="difference.line">first difference at line {{ difference.line }}</template>
          <template v-else>identical up to the end of the shorter version</template>
          <template v-if="difference.chars">, {{ difference.chars > 0 ? '+' : '' }}{{ difference.chars }} chars</template>
        </span>
        <Button variant="text" @click="emit('discard')">
          discard the draft and take the file
        </Button>
      </span>
    </div>

    <!-- Ordinary unsaved work: no alarm, but the way out is on screen. -->
    <div v-else-if="dirty" class="flex items-center gap-3 items-baseline unsaved">
      <span class="text-xs mono text-text-tertiary">
        unsaved
        <template v-if="difference.line">· first difference at line {{ difference.line }}</template>
        <template v-if="difference.chars">, {{ difference.chars > 0 ? '+' : '' }}{{ difference.chars }} chars</template>
      </span>
      <Button variant="text" @click="emit('discard')">discard</Button>
    </div>

    <!-- The editor is sized by its container and must not be allowed to
         collapse: `flex-1 min-h-0` is what gives it the pane's remaining height,
         and `--scroll-xl` is the floor under it on a phone, where the pane has
         no height of its own to share. -->
    <CodeEditor
      :model-value="modelValue"
      :doc-key="doc.key"
      :language="doc.format === 'yaml' ? 'yaml' : 'plain'"
      :readonly="doc.readonly"
      :label="doc.title"
      :fold-lines="foldLines"
      @update:model-value="emit('update:modelValue', $event)"
    />

    <footer class="foot flex items-center gap-3 text-xs text-text-tertiary">
      <span class="mono">{{ chars }} chars</span>
      <span v-if="doc.tokens_estimate" class="mono">≈{{ doc.tokens_estimate }} tokens for the model</span>
      <!-- The "⌘S to save" hint is gone. It hardcoded the Mac symbol, so on
           every other platform it named a chord that does not exist -- and the
           browser claims Ctrl+S for its own Save-page dialog, so the advice
           was wrong twice. The `mod+s` binding in `ConfigView` is still
           registered; the Save button is the affordance that actually works. -->
    </footer>
  </main>
</template>
