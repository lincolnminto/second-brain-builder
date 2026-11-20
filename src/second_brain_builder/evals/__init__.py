"""Development tools that measure the quality of what Ingest writes.

``python -m second_brain_builder.evals.faithfulness`` measures faithful compression
(ADR 0005): it samples claims of Wiki Pages, resolves the passage that each claim
cites, asks a judge model for a label, and reports the rate of each label.

- ``evals.claims``: claims (sentences with a footnote reference) and their sampling.
- ``evals.judge``: the judge prompt, its one structured tool call, and its parsing.
- ``evals.report``: the results, the rates per label and per page type, and the report.
- ``evals.faithfulness``: the command line, which reads a Brain through ``services``.

Reports hold Brain content. From a checkout they go to ``private/evals/`` at the
repository root, which git ignores; elsewhere to ``evals/`` in the data directory.
"""
