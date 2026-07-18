---
name: citation-management
description: Use to collect, normalize, verify, deduplicate, or export scientific references. Resolves DOI, PMID, arXiv, ISBN, accession, and title metadata against authoritative sources and creates traceable BibTeX without guessing fields.
---

# Citation management

Maintain references as verifiable research data. A citation is valid only when
its identifier and metadata resolve to the work actually supporting the claim.

## Source priority

Use the most authoritative record available:

1. DOI registration agency or discipline registry (Crossref, DataCite, PubMed);
2. official repository (arXiv, Zenodo, institutional or dataset repository);
3. publisher or conference record;
4. scholarly index as a discovery aid, not the final authority.

## Workflow

1. Extract every stable identifier and the nearby claim. Normalize DOI URLs to
   lowercase `10...` identifiers while retaining the original record.
2. Resolve the identifier and compare title, author list, year, venue, volume,
   pages or article number, version, and publication status.
3. For records without identifiers, search exact title plus first author and
   year. Keep them marked as title-resolved until an authoritative record is
   found.
4. Deduplicate by stable identifier, then normalized title/year. Do not merge a
   preprint and journal article unless the relationship is verified; retain the
   version actually cited.
5. Generate stable BibTeX keys from author/year/title and escape LaTeX safely.
   Preserve DOI, URL, PMID, arXiv ID, and access date where appropriate.
6. Validate that in-text keys exist in the bibliography and that each
   bibliography entry is cited. Report unresolved, duplicate, stale, or
   metadata-mismatched entries.

## Integrity rules

- Never invent missing authors, pages, issue numbers, DOIs, or titles.
- A resolvable paper is not automatically evidence for the adjacent claim;
  check topical and methodological support.
- Flag retractions, corrections, expressions of concern, preprints, and version
  changes.
- Keep a machine-readable `.bib` file separate from prose when possible.

Return the updated bibliography path, counts of verified/unresolved/duplicate
records, and a concise issue list.
