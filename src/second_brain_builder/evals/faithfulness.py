"""Measure how faithfully the Wiki Pages of a Brain compress their Sources (ADR 0005).

Run it where the app's data is, with the same ``SBB_*`` settings as the app::

    python -m second_brain_builder.evals.faithfulness --brain <id> --pages 20 --claims 5

It samples claims (sentences with a footnote reference) from random Wiki Pages,
resolves the passage that each claim cites with the citation resolver
(``services.resolve_citation``), and asks a judge, the configured Ingest Provider,
to label the claim: supported, partly supported, unsupported, opinion, or outside
knowledge. It prints the rate of each label, for all claims and for each page type,
and writes a JSON report (see ``default_report_dir``). The judge gets only the claim,
its page title, and its passage. The command reads the Brain and the database and
never changes them.

The exit status is 1 when the report cannot be written or when the judge labeled
no claim, for example because the passage index of the Brain is empty.
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
from collections.abc import Callable, Collection, Sequence
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import WIKI_DIR
from second_brain_builder.config import Settings, get_settings
from second_brain_builder.evals.claims import Claim, WikiPageText, cites_sentence, sample_claims
from second_brain_builder.evals.judge import judge_claim
from second_brain_builder.evals.report import (
    ClaimResult,
    FaithfulnessReport,
    build_report,
    format_report,
    write_report,
)
from second_brain_builder.providers import (
    ChatModel,
    ProviderAuthError,
    ProviderError,
    ProviderNotFoundError,
    Usage,
)
from second_brain_builder.services import (
    AppContext,
    CitationPassage,
    IngestModel,
    InvalidInputError,
    NotFoundError,
    ServiceError,
    get_ingest_model,
    list_pages,
    read_page,
    resolve_brain,
    resolve_citation,
)


class UnresolvedClaim(Exception):
    """A claim has no passage to judge. The message says why, without Source text."""


CitationResolver = Callable[[Claim], CitationPassage]
"""Gives the passage that a claim cites, or raises ``UnresolvedClaim``."""

ProgressCallback = Callable[[int, int, ClaimResult], None]
"""Gets the number of each finished claim (from 1), the claim count, and the result."""


def evaluate_claims(
    claims: Sequence[Claim],
    resolve: CitationResolver,
    judge: ChatModel,
    *,
    on_result: ProgressCallback | None = None,
) -> tuple[list[ClaimResult], Usage]:
    """Resolve the passage of each claim and ask the judge for its label.

    A claim without a passage is ``unresolved``, with the reason in ``error``, and
    is not sent to the judge. A failed Provider call makes the claim a
    ``provider_error``, and the next claims go on, except when the Provider rejects
    the key or does not know the model.

    Returns:
        The result of each claim, in order, and the tokens of all judge requests.

    Raises:
        ProviderAuthError, ProviderNotFoundError: The judge cannot answer any claim.
    """
    results: list[ClaimResult] = []
    usage = Usage()
    for number, claim in enumerate(claims, start=1):
        try:
            passage = resolve(claim)
        except UnresolvedClaim as exc:
            result = ClaimResult(claim=claim, status="unresolved", error=str(exc))
        else:
            try:
                outcome = judge_claim(
                    judge,
                    claim.sentence,
                    passage.text,
                    passage.heading_trail,
                    subject=claim.page_title,
                )
            except (ProviderAuthError, ProviderNotFoundError):
                raise
            except ProviderError as exc:
                result = ClaimResult(
                    claim=claim, status="provider_error", passage=passage, error=type(exc).__name__
                )
            else:
                usage = usage + outcome.usage
                verdict = outcome.verdict
                result = ClaimResult(
                    claim=claim,
                    status="judge_failed" if verdict is None else "judged",
                    passage=passage,
                    label=None if verdict is None else verdict.label,
                    reason=None if verdict is None else verdict.reason,
                )
        results.append(result)
        if on_result is not None:
            on_result(number, len(claims), result)
    return results, usage


def evaluate_brain(
    ctx: AppContext,
    brain_id: str,
    judge: IngestModel,
    *,
    page_count: int,
    claims_per_page: int,
    seed: int,
    only: Collection[str] | None = None,
    on_result: ProgressCallback | None = None,
) -> FaithfulnessReport:
    """Sample claims from the Wiki Pages of a Brain, judge them, and build the report.

    With ``only`` (page paths from the Brain root), pages are sampled from these
    Wiki Pages only, for example the pages that one Ingest created.

    Raises:
        NotFoundError: No such Brain.
        ProviderAuthError, ProviderNotFoundError: The judge cannot answer any claim.
    """
    paths = [
        summary.path
        for summary in list_pages(ctx, brain_id)
        if PurePosixPath(summary.path).is_relative_to(WIKI_DIR)
        and (only is None or summary.path in only)
    ]

    def read(path: str) -> WikiPageText | None:
        try:
            view = read_page(ctx, brain_id, path)
        except ServiceError:
            return None
        return WikiPageText(path=path, type=view.type, body=view.body, title=view.title)

    def resolve(claim: Claim) -> CitationPassage:
        try:
            passage = resolve_citation(ctx, brain_id, claim.page_path, claim.ref)
        except (NotFoundError, InvalidInputError) as exc:
            raise UnresolvedClaim(str(exc)) from None
        # The resolver reads the page again. When a job changed the page after
        # sampling, reference ``claim.ref`` can hold another sentence, and the
        # passage is then not the one that the claim cites. Read the page after
        # the resolver: when the sentence is the same before and after, the
        # resolver read it too.
        page = read(claim.page_path)
        if page is None or not cites_sentence(page, claim):
            raise UnresolvedClaim("The page changed after sampling.")
        return passage

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    claims = sample_claims(
        paths, read, page_count=page_count, claims_per_page=claims_per_page, rng=rng
    )
    results, usage = evaluate_claims(claims, resolve, judge.chat_model, on_result=on_result)
    return build_report(
        results,
        brain_id=brain_id,
        model=judge.model_name,
        created_at=ctx.clock(),
        seed=seed,
        pages_requested=page_count,
        claims_per_page=claims_per_page,
        usage=usage,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluation from the command line. Returns the exit status."""
    args = _parser().parse_args(argv)
    settings = get_settings()
    if not settings.database_path.is_file():
        _error(
            f"There is no database at {settings.database_path}. "
            "Set SBB_DATA_DIR to the data directory of the app."
        )
        return 1
    out = args.out if args.out is not None else default_report_dir(settings)
    problem = _unwritable(out)
    if problem is not None:
        _error(f"Cannot write a report to {out}: {problem}. Choose a directory with --out.")
        return 1
    only: set[str] | None = None
    if args.pages_from is not None:
        try:
            text = args.pages_from.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            _error(f"Cannot read {args.pages_from}: {exc}.")
            return 1
        only = {line.strip().lstrip("/") for line in text.splitlines() if line.strip()}
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**32)
    ctx = AppContext.create(settings, migrate_database=False)
    try:
        brain = resolve_brain(ctx, args.brain)
        judge = get_ingest_model(ctx)
        if judge is None:
            _error("No Ingest Provider is set. Set one in Settings, then run this again.")
            return 1
        report = evaluate_brain(
            ctx,
            brain.id,
            judge,
            page_count=args.pages,
            claims_per_page=args.claims,
            seed=seed,
            only=only,
            on_result=_print_progress,
        )
    except ServiceError as exc:
        _error(str(exc))
        return 1
    except ProviderError as exc:
        _error(f"The judge Provider failed: {exc}")
        return 1
    finally:
        ctx.close()
    if not report.results:
        _error("The sampled Wiki Pages have no footnote references, so there is nothing to judge.")
        return 1
    print(format_report(report))
    try:
        path = write_report(report, out)
    except OSError as exc:
        _error(f"Cannot write the report to {out}: {exc.strerror or exc}.")
        return 1
    print(f"\nReport: {path}")
    if report.overall.judged == 0:
        _error(_nothing_judged(report, brain.id))
        return 1
    return 0


def default_report_dir(settings: Settings) -> Path:
    """Where the report goes when ``--out`` is not given.

    Reports hold Brain content. In a checkout of the repository, they go to
    ``private/evals/`` at its root, which git ignores, from any working directory.
    Elsewhere, such as in the container, they go to ``evals/`` in the data
    directory, next to the database that holds the same content.
    """
    root = _checkout_root()
    if root is not None:
        return root / "private" / "evals"
    return settings.data_dir / "evals"


def _checkout_root() -> Path | None:
    """The repository root when this package runs from a checkout (``src/`` layout)."""
    package = Path(__file__).resolve().parents[1]
    root = package.parents[1]
    if package.parent.name == "src" and (root / ".git").exists():
        return root
    return None


def _unwritable(directory: Path) -> str | None:
    """None when a report can be written in ``directory``, which is made if needed.

    Otherwise the reason. The check runs before the judge, so a directory that
    cannot hold the report costs no Provider calls.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError as exc:
        return exc.strerror or str(exc)
    return None


def _nothing_judged(report: FaithfulnessReport, brain_id: str) -> str:
    overall = report.overall
    if overall.unresolved == overall.claims:
        return (
            "No claim has a resolved passage, so the judge labeled nothing. The passage "
            "index of this Brain can be empty or out of date, for example after an "
            "update. Run Sync Brain in the app (POST /api/brains/"
            f"{brain_id}/sync), wait for the job to finish, and run this again."
        )
    return (
        f"The judge labeled no claim: {overall.failed} failed and {overall.unresolved} "
        "are unresolved. The report gives the reason for each claim."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m second_brain_builder.evals.faithfulness",
        description=(
            "Measure how faithfully the Wiki Pages of a Brain compress their Sources. "
            "The judge is the Ingest Provider; it gets only each claim, its page title, "
            "and its passage."
        ),
    )
    parser.add_argument(
        "--brain", required=True, help="the Brain: its id, repository (owner/name), or name"
    )
    parser.add_argument(
        "--pages", type=_positive, default=20, help="Wiki Pages to sample (default: 20)"
    )
    parser.add_argument(
        "--claims",
        type=_positive,
        default=5,
        help="the most claims to sample from each page (default: 5)",
    )
    parser.add_argument(
        "--seed", type=int, help="seed of the sampling (default: random; the report keeps it)"
    )
    parser.add_argument(
        "--pages-from",
        type=Path,
        help=(
            "a text file with one page path per line (such as wiki/entities/grey-heron.md): "
            "sample only these Wiki Pages, for example the pages that one Ingest created"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "directory of the JSON report (default: private/evals/ at the root of a "
            "checkout, else evals/ in the data directory)"
        ),
    )
    return parser


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _print_progress(number: int, total: int, result: ClaimResult) -> None:
    outcome = result.label.value if result.label is not None else result.status
    line = f"[{number}/{total}] {outcome}: {result.claim.page_path} (reference {result.claim.ref})"
    if result.status == "unresolved" and result.error:
        line += f": {result.error}"
    print(line, file=sys.stderr)


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
