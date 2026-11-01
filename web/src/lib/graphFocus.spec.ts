import { describe, expect, it } from "vitest";

import { neighbourhoodIds } from "./graphFocus";

import type { GraphEdge } from "@/types/graph";

/**
 * The focus walk has to spell the filter rules exactly as `applyVisibility`
 * spells them. It is the only part of that agreement testable without a canvas,
 * and both of the disagreements below were live: a node in scope with no drawn
 * connection to its subject, or an edge drawn to a node the scope dropped.
 */
function edge(
  source: string,
  target: string,
  over: Partial<GraphEdge> = {},
): GraphEdge {
  return {
    id: `${source}|PART_OF|${target}`,
    source,
    target,
    predicate: "PART_OF",
    source_count: 2,
    declared: false,
    confidence: 0.9,
    kind: null,
    first_seen: null,
    last_seen: null,
    ...over,
  };
}

const typeOf = (id: string) => id.split(":")[0];

describe("neighbourhoodIds", () => {
  it("walks one hop in either direction", () => {
    const scope = neighbourhoodIds(
      [edge("company:a", "tech:b"), edge("prog:c", "company:a")],
      "company:a",
      1,
      { predicates: [], minSourceCount: 1 },
    );
    expect([...scope].sort()).toEqual(["company:a", "prog:c", "tech:b"]);
  });

  it("stops at a predicate the reader deselected", () => {
    const scope = neighbourhoodIds(
      [edge("company:a", "tech:b")],
      "company:a",
      2,
      {
        predicates: ["USES_TECH"],
        minSourceCount: 1,
      },
    );
    expect([...scope]).toEqual(["company:a"]);
  });

  it("keeps a declared edge above the source floor", () => {
    // A declaration names no article, so its `source_count` is a floor artefact
    // rather than a publisher tally. Every other surface exempts it; a walk
    // that did not dropped the org chart the moment the slider moved, while the
    // canvas went on drawing it.
    const scope = neighbourhoodIds(
      [edge("company:a", "govt:b", { declared: true, source_count: 1 })],
      "company:a",
      1,
      { predicates: [], minSourceCount: 3 },
    );
    expect([...scope].sort()).toEqual(["company:a", "govt:b"]);
  });

  it("drops an undeclared edge below the source floor", () => {
    const scope = neighbourhoodIds(
      [edge("company:a", "govt:b", { source_count: 1 })],
      "company:a",
      1,
      { predicates: [], minSourceCount: 3 },
    );
    expect([...scope]).toEqual(["company:a"]);
  });

  it("admits a cross-type edge through either endpoint", () => {
    // The rule that stopped a single-type selection emptying the canvas. Here
    // the subject is the unselected end and the neighbour is the selected one.
    const scope = neighbourhoodIds([edge("prog:a", "tech:b")], "prog:a", 1, {
      predicates: [],
      minSourceCount: 1,
      types: ["Technology"],
      typeOf: (id) => (typeOf(id) === "tech" ? "Technology" : "Program"),
    });
    expect([...scope].sort()).toEqual(["prog:a", "tech:b"]);
  });

  it("stops at an edge neither of whose ends a chip selected", () => {
    const scope = neighbourhoodIds([edge("prog:a", "company:b")], "prog:a", 1, {
      predicates: [],
      minSourceCount: 1,
      types: ["Technology"],
      typeOf: () => "Program",
    });
    expect([...scope]).toEqual(["prog:a"]);
  });

  it("treats no chip as every type, which is not the same as none", () => {
    const scope = neighbourhoodIds([edge("prog:a", "company:b")], "prog:a", 1, {
      predicates: [],
      minSourceCount: 1,
      types: [],
      typeOf: () => "Program",
    });
    expect([...scope].sort()).toEqual(["company:b", "prog:a"]);
  });
});
