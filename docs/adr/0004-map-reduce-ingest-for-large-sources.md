# Large Sources use map-reduce Ingest

Sequential Ingest works Part by Part, but on a 1 M-word book it costs about 300k tokens per Part (the agent re-reads and rewrites the same entity pages in every Part) and takes about 1.7 hours for 128 Parts. For Sources above a Part threshold, Ingest runs in two phases: a **map** phase, where read-only extractors (a cheap model, in parallel, optionally through the Batch API) turn each Part into structured notes (entities, concepts, and claims, each with its citation), and a **reduce** phase, where code groups the notes by normalized name, one merge step resolves aliases (for example "Abrão" and "Abraão"), and a single writer writes or updates each page once with all its citations. We keep one writer so the Brain invariants hold (search before create, read before update, one commit stream per Brain).

## Update after the first real run (2026-11-19)

A 128-Part book gave 2,131 note groups; one writer would need hours, and the largest group (hundreds of facts) grew its page update by update until a task hit its token cap. So the reduce phase now (1) gives a page only to groups with at least 2 facts, (2) condenses a large group's facts before it writes the page once, (3) isolates a failed group instead of failing the job, and (4) runs several writers in parallel, each on a different page. Parallel writers are safe here because grouping assigns every page to exactly one group before the reduce starts; commits, pushes, and progress writes stay serialized.

The second real run wrote about 8 pages per minute at about 22k input tokens per page, with few links between pages. So each group now gets one write request (plus one corrective turn on validation errors). The request carries the existing page from disk and up to 30 related pages, ranked by shared Parts, with their final paths, so the writer can link to them. Eight writers run by default. A group gets a page when its facts come from at least two Parts or it has at least four facts. Condensation works in batches of at most 60 facts.

The frequency filter skipped two in three names of the Bible, including important rare ones such as "Torre de Babel", and their facts reached no page. So rare groups now go through a triage: the model marks the major ones in batches of 150, and those get pages too. Every group still without a page goes into Name Index pages that code builds (no LLM): split by first letter, under 20,000 characters each, with every entry cited. A run with `resume=false` reuses the stored map results and restarts the reduce.

## Considered Options

- **Parallel writer agents per chapter:** rejected. They collide on shared pages (one person appears in hundreds of chapters), which causes duplicate pages, lost updates, and git conflicts.
- **Sequential Ingest only:** kept for small Sources, where its cost is low and its pages read well.

## Consequences

- Map results are stored per Part, so a run can stop and resume like sequential Ingest, and a failed Part is extracted again without repeating the others.
- The extraction schema is versioned; a schema change invalidates stored map results.
- The page limit, the token budget, and the estimate apply per phase.
