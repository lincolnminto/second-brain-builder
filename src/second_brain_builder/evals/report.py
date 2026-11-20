"""The results of a faithfulness evaluation, the rates of each label, and the report file."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from second_brain_builder.evals.claims import Claim
from second_brain_builder.evals.judge import FaithfulnessLabel
from second_brain_builder.providers import Usage
from second_brain_builder.services import CitationPassage

ClaimStatus = Literal["judged", "unresolved", "judge_failed", "provider_error"]
"""What happened to one claim.

``judged``: the judge gave a label. ``unresolved``: the citation resolver found no
passage, or the page changed after sampling, so nothing was sent to the judge.
``judge_failed``: no answer of the judge had a valid ``record_label`` call.
``provider_error``: the Provider call failed.
"""


class ClaimResult(BaseModel):
    """One claim, the passage that it cites, and the label of the judge."""

    model_config = ConfigDict(frozen=True)

    claim: Claim
    status: ClaimStatus
    passage: CitationPassage | None = None
    label: FaithfulnessLabel | None = None
    reason: str | None = None
    error: str | None = None
    """Why the claim has no label. For ``unresolved``: why it has no passage. For
    ``provider_error``: the name of the error class, never its message."""


class LabelRates(BaseModel):
    """The labels of a group of claims.

    ``rates`` are fractions of the judged claims (0 when no claim was judged).
    Unresolved and failed claims have no label, so they are not in the rates.
    """

    claims: int
    judged: int
    unresolved: int
    failed: int
    counts: dict[FaithfulnessLabel, int]
    rates: dict[FaithfulnessLabel, float]


class FaithfulnessReport(BaseModel):
    """The report of one faithfulness evaluation of a Brain."""

    brain_id: str
    model: str
    """The model name of the judge (the Ingest Provider's model)."""
    created_at: datetime
    seed: int
    """The seed of the sampling. The same seed and the same pages give the same claims."""
    pages_requested: int
    claims_per_page: int
    pages_sampled: int
    usage: Usage
    """The tokens of every judge request."""
    overall: LabelRates
    by_page_type: dict[str, LabelRates]
    results: list[ClaimResult]


def label_rates(results: Sequence[ClaimResult]) -> LabelRates:
    """The count and the rate of each label, and the claims without a label."""
    counts = dict.fromkeys(FaithfulnessLabel, 0)
    for result in results:
        if result.status == "judged" and result.label is not None:
            counts[result.label] += 1
    judged = sum(counts.values())
    return LabelRates(
        claims=len(results),
        judged=judged,
        unresolved=sum(1 for result in results if result.status == "unresolved"),
        failed=sum(1 for result in results if result.status in ("judge_failed", "provider_error")),
        counts=counts,
        rates={label: count / judged if judged else 0.0 for label, count in counts.items()},
    )


def build_report(
    results: Sequence[ClaimResult],
    *,
    brain_id: str,
    model: str,
    created_at: datetime,
    seed: int,
    pages_requested: int,
    claims_per_page: int,
    usage: Usage,
) -> FaithfulnessReport:
    """The report of ``results``: the rates of all claims and of each page type."""
    page_types = sorted({result.claim.page_type for result in results})
    return FaithfulnessReport(
        brain_id=brain_id,
        model=model,
        created_at=created_at,
        seed=seed,
        pages_requested=pages_requested,
        claims_per_page=claims_per_page,
        pages_sampled=len({result.claim.page_path for result in results}),
        usage=usage,
        overall=label_rates(results),
        by_page_type={
            page_type: label_rates([r for r in results if r.claim.page_type == page_type])
            for page_type in page_types
        },
        results=list(results),
    )


def write_report(report: FaithfulnessReport, directory: Path) -> Path:
    """Write ``report`` as JSON to a new file in ``directory`` and return its path.

    The name is ``faithfulness-<brain id>-<UTC time>.json``. The directory is made
    when it does not exist.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.created_at.strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"faithfulness-{report.brain_id}-{stamp}.json"
    number = 1
    while path.exists():
        number += 1
        path = directory / f"faithfulness-{report.brain_id}-{stamp}-{number}.json"
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def format_report(report: FaithfulnessReport) -> str:
    """The rates of each label, for all claims and for each page type, as a text table."""
    overall = report.overall
    lines = [
        f"Faithfulness of Brain {report.brain_id}: {overall.claims} claims from "
        f"{report.pages_sampled} pages; {overall.judged} judged, {overall.unresolved} "
        f"unresolved, {overall.failed} failed. Judge: {report.model}.",
        "",
    ]
    headers = ["page type", "claims", "judged", *(label.value for label in FaithfulnessLabel)]
    rows = [_row("all", overall)]
    rows += [_row(page_type, rates) for page_type, rates in report.by_page_type.items()]
    widths = [max(len(row[column]) for row in [headers, *rows]) for column in range(len(headers))]
    for row in [headers, *rows]:
        cells = [row[0].ljust(widths[0])]
        cells += [cell.rjust(width) for cell, width in zip(row[1:], widths[1:], strict=True)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def _row(name: str, rates: LabelRates) -> list[str]:
    return [
        name,
        str(rates.claims),
        str(rates.judged),
        *(f"{rates.rates[label]:.1%}" for label in FaithfulnessLabel),
    ]
