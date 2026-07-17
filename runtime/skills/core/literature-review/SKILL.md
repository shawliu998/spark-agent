---
name: literature-review
description: Use for a scoping, narrative, or systematic review that must search multiple scholarly sources, screen and deduplicate results, assess evidence, verify citations, and produce a structured synthesis with an evidence matrix.
references:
  - literature_bundle.py
---

# Literature review

Conduct a transparent, bounded review whose depth matches the user's question.
Use a PRISMA-style record for a systematic review; do not add ceremony to a
small background scan.

## Protocol

Before searching, define:

- research question and facets;
- population, intervention/exposure, comparison, outcomes, or domain analogues;
- eligible designs and source types;
- date, language, and publication-status limits;
- exclusion rules and stopping condition.

Record the search date, database/source, and exact query. Search at least two
appropriate credential-free sources when available, such as OpenAlex, Crossref,
PubMed/Europe PMC, or arXiv. Delegate independent facets only when breadth
justifies it. Normalize source responses as JSON envelopes with a `source` and
`records` array, then run the bundled helper:

```bash
python "$XDG_CONFIG_HOME/opencode/skills/literature-review/literature_bundle.py" \
  --input references/openalex.json --input references/pubmed.json \
  --output-dir . --question "<research question>"
```

The helper writes `references/corpus.csv`, `references/references.bib`, and
`reports/literature-review.md`. It deduplicates using DOI, PMID, arXiv ID, or
normalized title/year and never creates a missing identifier. Include backward
or forward citation chasing when it can materially change coverage.

Screen title/abstract first and full text second when full text is available.
Never infer full-text methods from an abstract. For each included source capture
design, sample, setting, method, outcome, main result, uncertainty, limitations,
and relevance. Distinguish peer-reviewed articles, preprints, protocols,
registrations, datasets, and secondary reviews.

Synthesize by question or theme rather than listing papers one by one. Explain
contradictions through differences in design, population, measurement, method,
bias, or precision. Do not count papers as votes. Evaluate evidence quality and
state where publication bias or missing data could distort the picture.

Verify every included reference against a registry, repository, or publisher
record. Mark inaccessible or unresolved references; never fabricate metadata.

## Deliverables

Produce:

1. scope and protocol;
2. sources, queries, dates, and screening counts;
3. evidence matrix;
4. thematic findings and conflicts;
5. evidence gaps and limits;
6. verified references, plus BibTeX when requested;
7. screening log for a systematic review.

Stop when predefined coverage is met and new searches add no material themes or
eligible evidence.

If only one source is available, preserve that limitation in the generated
report rather than implying multi-source coverage.
