---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Acronym Tier 3 re-architected: the model never quotes itself


**Measured.** `WebFetch` does not hand the model raw page text — it runs a
sub-model that summarises the page. So a "verbatim quote" the model returns is
drawn from *that summary*, not the source. Re-fetching the cited URL and
checking the quote appears in it **failed on a correct answer**.

The original verification chain would have rejected most correct answers while
appearing rigorous.

**Now:** the model returns only candidate URLs; **we** fetch and extract with
trafilatura; a second no-tools call quotes from text we supplied; we verify the
quote against our own extracted text. The model is a search tool and a reader
over text we control — never a source of facts.
