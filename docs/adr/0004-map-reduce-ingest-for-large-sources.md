# Large Sources use map-reduce Ingest

Sequential Ingest works Part by Part, but on a 1 M-word book it costs about 300k tokens per Part (the agent re-reads and rewrites the same entity pages in every Part) and takes about 1.7 hours for 128 Parts. For Sources above a Part threshold, Ingest runs in two phases: a **map** phase, where read-only extractors (a cheap model, in parallel, optionally through the Batch API) turn each Part into structured notes (entities, concepts, and claims, each with its citation), and a **reduce** phase, where code groups the notes by normalized name, one merge step resolves aliases (for example "Abrão" and "Abraão"), and a single writer writes or updates each page once with all its citations. We keep one writer so the Brain invariants hold (search before create, read before update, one commit stream per Brain).

## Considered Options

- **Parallel writer agents per chapter:** rejected. They collide on shared pages (one person appears in hundreds of chapters), which causes duplicate pages, lost updates, and git conflicts.
- **Sequential Ingest only:** kept for small Sources, where its cost is low and its pages read well.

## Consequences

- Map results are stored per Part, so a run can stop and resume like sequential Ingest, and a failed Part is extracted again without repeating the others.
- The extraction schema is versioned; a schema change invalidates stored map results.
- The page limit, the token budget, and the estimate apply per phase.
