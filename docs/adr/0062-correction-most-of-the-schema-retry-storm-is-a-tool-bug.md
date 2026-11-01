---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Correction: most of the "schema retry storm" is a tool bug


An earlier entry today called schema rejections "the single largest avoidable
cost in a run". That was measured wrong, and the correction matters more than
the original claim.

Classifying every rejection by the tool call that *caused* it — join each
`tool_result` error to the `tool_use` immediately before it, which the
transcript now makes possible — splits them cleanly:

```
analyze     78 of 117   $PARAMETER_NAME artifact
            39 of 117   genuine schema mistakes
discovery   13 of 13    genuine
```

The artifact is the model emitting the literal tool-call placeholders
`$PARAMETER_NAME` / `$PARAMETER_VALUE`, with the **correct payload nested inside
`$PARAMETER_VALUE`**. The schema is understood perfectly; the failure is at the
invocation layer. Two thirds of analyze rejections are therefore not avoidable
by anything written in a prompt, and restating the contract for them is
explaining the answer to someone who already has it.

What the `output_contract` change does fix is the genuine remainder. On
discovery, where every rejection is genuine and the sample is clean:

```
0.75 genuine rejections per call  ->  0.13     (two baseline runs, one after)
```

An 83% drop. The analyze effect is not yet measurable: the only run since the
change analysed 14 clusters, and those were the deferred leftovers — the longest
documents, selected precisely because earlier runs could not finish them.

**Two lessons.**

The first is about the claim: "105 of 222 calls were rejected" was a real
number attached to a wrong cause, and a plausible fix aimed at it would have
looked successful on the cost line while addressing a third of the problem.

The second is about the transcript. This diagnosis is only possible because
`model_message` records the tool input alongside the result — the `llm_calls`
ledger showed 2 terminal errors out of 221 and nothing else. The observability
work paid for itself by falsifying a conclusion drawn a few hours earlier.
