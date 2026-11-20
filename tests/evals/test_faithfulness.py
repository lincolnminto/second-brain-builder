"""The faithfulness evaluation with a scripted judge and a fake citation resolver."""

from __future__ import annotations

import itertools
import random
import shutil
import subprocess
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pytest

from second_brain_builder.brains import BrainSettings, scaffold_brain
from second_brain_builder.config import Settings
from second_brain_builder.domain import BrainStatus
from second_brain_builder.evals import faithfulness
from second_brain_builder.evals.claims import Claim, WikiPageText, sample_claims
from second_brain_builder.evals.faithfulness import (
    CitationResolver,
    UnresolvedClaim,
    default_report_dir,
    evaluate_brain,
    evaluate_claims,
    main,
)
from second_brain_builder.evals.judge import (
    JUDGE_TOOL_NAME,
    FaithfulnessLabel,
    judge_claim,
)
from second_brain_builder.evals.report import (
    FaithfulnessReport,
    build_report,
    format_report,
    write_report,
)
from second_brain_builder.providers import (
    ChatResponse,
    Message,
    ProviderAuthError,
    ProviderRateLimitError,
    ToolCall,
    ToolSpec,
    Usage,
)
from second_brain_builder.services import AppContext, CitationPassage, IngestModel
from second_brain_builder.store import BrainRow

INGEST_BRAIN = Path(__file__).parents[1] / "fixtures" / "ingest" / "brain"
REPO_ROOT = Path(__file__).resolve().parents[2]

_ids = itertools.count()


@dataclass
class Request:
    system: str
    messages: list[Message]
    tools: list[ToolSpec]
    effort: str | None


@dataclass
class ScriptedJudge:
    """A ChatModel that replays planned answers (or raises planned errors) in order."""

    steps: deque[ChatResponse | Exception]
    requests: list[Request] = field(default_factory=list)

    def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        *,
        effort: Literal["low", "medium", "high"] | None = None,
    ) -> ChatResponse:
        copied = [message.model_copy(deep=True) for message in messages]
        self.requests.append(Request(system, copied, list(tools), effort))
        assert self.steps, "the judge got more requests than the script has answers"
        step = self.steps.popleft()
        if isinstance(step, Exception):
            raise step
        return step


def judge(*steps: ChatResponse | Exception) -> ScriptedJudge:
    return ScriptedJudge(deque(steps))


def label(value: Any, reason: Any = "The passage says so.", **extra: Any) -> ChatResponse:
    arguments = {"label": value, "reason": reason, **extra}
    return ChatResponse(
        tool_calls=[ToolCall(id=f"j-{next(_ids)}", name=JUDGE_TOOL_NAME, arguments=arguments)],
        usage=Usage(input_tokens=100, output_tokens=10),
        stop_reason="tool_use",
    )


def text_only() -> ChatResponse:
    return ChatResponse(text="Supported.", usage=Usage(input_tokens=100), stop_reason="end_turn")


def passage(text: str) -> CitationPassage:
    # As in real Canonical Documents, the trail starts with the "#" document title.
    return CitationPassage(
        source_id="tide-pools",
        source_path="sources/nature/tide-pools/tide-pools.md",
        source_title="Tide Pools of the Example Coast",
        char_start=10,
        char_end=10 + len(text),
        heading_trail=["Tide Pools of the Example Coast", "Visitors"],
        text=text,
        score=0.5,
        method="terms",
    )


def claim(page: str, page_type: str, ref: int, sentence: str) -> Claim:
    return Claim(
        page_path=page, page_type=page_type, ref=ref, source_id="tide-pools", sentence=sentence
    )


def resolver(passages: Mapping[tuple[str, int], CitationPassage]) -> CitationResolver:
    """A fake citation resolver: the passage of each (page, reference), else unresolved."""

    def resolve(claim: Claim) -> CitationPassage:
        found = passages.get((claim.page_path, claim.ref))
        if found is None:
            raise UnresolvedClaim("No passage matches this citation.")
        return found

    return resolve


def add_fixture_brain(ctx: AppContext, *, index: bool = True) -> tuple[str, Path]:
    """A ready Brain whose clone is a copy of the Ingest fixture Brain."""
    with ctx.sessions.begin() as session:
        row = BrainRow(
            name="Shore Notes", repo_full_name="test-user/shore-notes", status=BrainStatus.READY
        )
        session.add(row)
        session.flush()
        brain_id = row.id
    root = ctx.clone_path(brain_id)
    shutil.copytree(INGEST_BRAIN, root)
    scaffold_brain(
        root,
        BrainSettings(
            name="Shore Notes",
            language="en",
            created_at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
        ),
    )
    if index:
        ctx.search.rebuild(brain_id, root)
    return brain_id, root


HERON = """# Grey Heron

The grey heron wades into rock pools.[^tide-pools] It hunts small fish at low tide.[^birds]

- It nests in tall trees (chapter 2).[^birds]

[^tide-pools]: [Tide Pools](/sources/nature/tide-pools/tide-pools.md)
[^birds]: [Coastal Birds](/sources/nature/coastal-birds/coastal-birds.md), a note.[^tide-pools]
"""
STAR = "# Sea Star\n\nSea stars cling to the rocks.[^tide-pools]\n\n[^tide-pools]: Tide Pools\n"
WADING = "# Wading\n\nWading birds walk in shallow water.\n"
PAGES = {
    "wiki/entities/grey-heron.md": WikiPageText("wiki/entities/grey-heron.md", "entity", HERON),
    "wiki/entities/sea-star.md": WikiPageText("wiki/entities/sea-star.md", "entity", STAR),
    "wiki/concepts/wading.md": WikiPageText("wiki/concepts/wading.md", "concept", WADING),
}
UNREADABLE = "wiki/concepts/broken.md"


def read(path: str) -> WikiPageText | None:
    return PAGES.get(path)


def test_sampling_takes_body_references_with_their_sentences() -> None:
    paths = [*PAGES, UNREADABLE]

    claims = sample_claims(paths, read, page_count=10, claims_per_page=10, rng=random.Random(1))

    # The reference in a footnote definition is not a claim; pages without
    # references or that cannot be read give none.
    assert sorted((c.page_path, c.ref, c.source_id, c.sentence) for c in claims) == [
        ("wiki/entities/grey-heron.md", 0, "tide-pools", "The grey heron wades into rock pools."),
        ("wiki/entities/grey-heron.md", 1, "birds", "It hunts small fish at low tide."),
        ("wiki/entities/grey-heron.md", 2, "birds", "It nests in tall trees (chapter 2)."),
        ("wiki/entities/sea-star.md", 0, "tide-pools", "Sea stars cling to the rocks."),
    ]
    assert {c.page_type for c in claims} == {"entity"}


def test_sampling_reads_caret_brackets_as_text_not_as_references() -> None:
    # OKF and GFM have no inline footnotes: "^[...]" is text that a page can hold.
    body = (
        "# Patterns\n\nThe pattern ^[A-Z] matches a capital.[^tide-pools] "
        "Herons wade.^[Not a footnote.]\n\n[^tide-pools]: Tide Pools\n"
    )
    page = WikiPageText("wiki/concepts/patterns.md", "concept", body)

    claims = sample_claims(
        [page.path], lambda path: page, page_count=1, claims_per_page=5, rng=random.Random(1)
    )

    assert [(c.ref, c.sentence) for c in claims] == [(0, "The pattern ^[A-Z] matches a capital.")]


def test_sampling_limits_pages_and_claims_and_repeats_with_the_seed() -> None:
    paths = [*PAGES, UNREADABLE]

    first = sample_claims(paths, read, page_count=1, claims_per_page=2, rng=random.Random(7))
    again = sample_claims(paths, read, page_count=1, claims_per_page=2, rng=random.Random(7))

    assert first == again
    assert len({c.page_path for c in first}) == 1
    assert 1 <= len(first) <= 2
    assert [c.ref for c in first] == sorted(c.ref for c in first)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("supported", FaithfulnessLabel.SUPPORTED),
        ("Partly supported", FaithfulnessLabel.PARTLY_SUPPORTED),
        ("outside-knowledge", FaithfulnessLabel.OUTSIDE_KNOWLEDGE),
        ("  OPINION ", FaithfulnessLabel.OPINION),
    ],
)
def test_judge_reads_the_label_in_its_usual_spellings(
    answer: str, expected: FaithfulnessLabel
) -> None:
    model = judge(label(answer, reason="  One   short\nreason. ", confidence=0.9))

    outcome = judge_claim(model, "Sea stars cling to the rocks.", "Sea stars cling to rocks.")

    assert outcome.verdict is not None
    assert (outcome.verdict.label, outcome.verdict.reason) == (expected, "One short reason.")
    assert outcome.requests == 1


@pytest.mark.parametrize(
    "first", [label("maybe"), label(None), text_only()], ids=["unknown", "missing", "no-call"]
)
def test_judge_gets_one_correction_without_its_own_output(first: ChatResponse) -> None:
    model = judge(first, label("unsupported"))

    outcome = judge_claim(model, "Herons sing at dawn.", "Herons wait on the dunes.")

    assert outcome.verdict is not None
    assert outcome.verdict.label is FaithfulnessLabel.UNSUPPORTED
    assert outcome.usage == Usage(input_tokens=200, output_tokens=10 * (len(first.tool_calls) + 1))
    correction = model.requests[1].messages
    assert correction[-1].role == "user" and "partly_supported" in (correction[-1].content or "")
    assert all("maybe" not in (message.content or "") for message in correction[2:])


def test_judge_gives_up_after_one_correction() -> None:
    model = judge(label("maybe"), label("probably"))

    outcome = judge_claim(model, "Herons sing at dawn.", "Herons wait on the dunes.")

    assert outcome.verdict is None
    assert outcome.requests == 2 and not model.steps


def test_evaluation_rates_per_label_and_page_type(tmp_path: Path) -> None:
    claims = [
        claim("wiki/entities/grey-heron.md", "entity", 0, "Herons wade into rock pools."),
        claim("wiki/entities/grey-heron.md", "entity", 1, "Herons arrived in 1850."),
        claim("wiki/entities/sea-star.md", "entity", 0, "Sea stars cling to rocks."),
        claim("wiki/concepts/tides.md", "concept", 0, "The tides are beautiful."),
        claim("wiki/concepts/tides.md", "concept", 1, "Tides come twice a day."),
        claim("wiki/summaries/tide-pools.md", "summary", 0, "Pools hold crabs and stars."),
    ]
    passages = {
        ("wiki/entities/grey-heron.md", 0): passage("Grey herons wade into the pools."),
        ("wiki/entities/grey-heron.md", 1): passage("Grey herons came to the coast."),
        ("wiki/concepts/tides.md", 0): passage("The tide rises over the rocks."),
        ("wiki/concepts/tides.md", 1): passage("The tide rises twice a day."),
        ("wiki/summaries/tide-pools.md", 0): passage("The pools hold hermit crabs."),
    }
    model = judge(
        label("supported"),
        label("Outside knowledge"),
        label("opinion"),
        label("maybe"),
        label("maybe"),
        label("partly-supported"),
    )

    results, usage = evaluate_claims(claims, resolver(passages), model)
    report = build_report(
        results,
        brain_id="example-brain",
        model="scripted",
        created_at=datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc),
        seed=7,
        pages_requested=4,
        claims_per_page=2,
        usage=usage,
    )

    assert [result.status for result in results] == [
        "judged", "judged", "unresolved", "judged", "judge_failed", "judged"
    ]  # fmt: skip
    assert results[2].error == "No passage matches this citation."
    overall = report.overall
    assert (overall.claims, overall.judged, overall.unresolved, overall.failed) == (6, 4, 1, 1)
    assert overall.rates == {
        FaithfulnessLabel.SUPPORTED: 0.25,
        FaithfulnessLabel.PARTLY_SUPPORTED: 0.25,
        FaithfulnessLabel.UNSUPPORTED: 0.0,
        FaithfulnessLabel.OPINION: 0.25,
        FaithfulnessLabel.OUTSIDE_KNOWLEDGE: 0.25,
    }
    entity = report.by_page_type["entity"]
    assert (entity.claims, entity.judged, entity.unresolved) == (3, 2, 1)
    assert entity.rates[FaithfulnessLabel.SUPPORTED] == 0.5
    assert entity.rates[FaithfulnessLabel.OUTSIDE_KNOWLEDGE] == 0.5
    concept = report.by_page_type["concept"]
    assert (concept.judged, concept.failed, concept.rates[FaithfulnessLabel.OPINION]) == (1, 1, 1.0)
    assert report.by_page_type["summary"].rates[FaithfulnessLabel.PARTLY_SUPPORTED] == 1.0
    assert (report.pages_sampled, report.usage) == (4, Usage(input_tokens=600, output_tokens=60))
    assert "25.0%" in format_report(report)

    # The judge gets the claim and the passage with its heading trail, which is text
    # of the Canonical Document, and nothing from the page, the Brain, or the index.
    first = model.requests[0]
    assert (first.effort, [tool.name for tool in first.tools]) == ("low", [JUDGE_TOOL_NAME])
    sent = first.system + "".join(message.content or "" for message in first.messages)
    assert "Herons wade into rock pools." in sent
    assert "Grey herons wade into the pools." in sent
    assert "Tide Pools of the Example Coast > Visitors" in sent
    for private in ("grey-heron", "tide-pools", "sources/nature", "example-brain"):
        assert private not in sent

    path = write_report(report, tmp_path / "evals")
    assert path.parent == tmp_path / "evals"
    assert FaithfulnessReport.model_validate_json(path.read_text()) == report


def test_provider_errors_skip_a_claim_but_a_rejected_key_stops_the_run() -> None:
    claims = [
        claim("wiki/entities/sea-star.md", "entity", 0, "Sea stars cling to rocks."),
        claim("wiki/entities/sea-star.md", "entity", 1, "Sea stars eat mussels."),
    ]
    found = passage("Sea stars cling to the rocks and eat mussels.")
    model = judge(ProviderRateLimitError("Too many requests.", status_code=429), label("supported"))

    results, _ = evaluate_claims(claims, lambda claim: found, model)

    assert [(r.status, r.error) for r in results] == [
        ("provider_error", "ProviderRateLimitError"),
        ("judged", None),
    ]
    with pytest.raises(ProviderAuthError):
        evaluate_claims(
            claims, lambda claim: found, judge(ProviderAuthError("Bad key.", status_code=401))
        )


def test_evaluate_brain_judges_wiki_claims_against_their_resolved_passages(
    settings: Settings,
) -> None:
    ctx = AppContext.create(settings)
    try:
        brain_id, root = add_fixture_brain(ctx)
        model = judge(*[label("supported")] * 60)

        report = evaluate_brain(
            ctx,
            brain_id,
            IngestModel(chat_model=model, model_name="scripted", provider_id=1),
            page_count=20,
            claims_per_page=3,
            seed=3,
        )
    finally:
        ctx.close()

    judged = [result for result in report.results if result.status == "judged"]
    assert judged and report.overall.rates[FaithfulnessLabel.SUPPORTED] == 1.0
    assert all(result.claim.page_path.startswith("wiki/") for result in report.results)
    for result in judged:
        assert result.passage is not None
        assert result.passage.source_id == result.claim.source_id
        canonical = (root / result.passage.source_path).read_text(encoding="utf-8")
        assert result.passage.text.split()[0] in canonical
    assert len(model.requests) == len(judged)
    for request, result in zip(model.requests, judged, strict=True):
        assert result.passage is not None
        sent = "".join(message.content or "" for message in request.messages)
        assert result.claim.sentence in sent and result.passage.text in sent
        # The page title is the subject that a sentence of an entity page leaves out.
        assert result.claim.page_title and result.claim.page_title in sent
        assert result.claim.page_path not in sent and brain_id not in sent
        assert result.passage.source_path not in sent
    assert {result.claim.page_type for result in report.results} <= {
        "summary", "entity", "concept", "comparison"
    }  # fmt: skip


def test_a_page_that_changes_after_sampling_is_not_judged(settings: Settings) -> None:
    ctx = AppContext.create(settings)
    heron = "wiki/entities/grey-heron.md"
    try:
        brain_id, root = add_fixture_brain(ctx)
        page = root / heron

        def ingest_meanwhile(number: int, total: int, result: object) -> None:
            # A job writes a new cited sentence at the top of the page after the
            # first claim, so each later reference number holds another sentence.
            if number == 1:
                text = page.read_text(encoding="utf-8")
                new = "# Grey Heron\n\nHerons nest in colonies.[^tide-pools]\n\n"
                page.write_text(text.replace("# Grey Heron\n\n", new, 1), encoding="utf-8")

        model = judge(*[label("supported")] * 60)
        report = evaluate_brain(
            ctx,
            brain_id,
            IngestModel(chat_model=model, model_name="scripted", provider_id=1),
            page_count=20,
            claims_per_page=3,
            seed=3,
            on_result=ingest_meanwhile,
        )
    finally:
        ctx.close()

    changed = [r for r in report.results[1:] if r.claim.page_path == heron]
    assert changed, "the seed must sample the heron page after the first claim"
    assert all(
        (r.status, r.error) == ("unresolved", "The page changed after sampling.") for r in changed
    )
    # One judge request per judged claim: the changed claims were not sent.
    judged = [r for r in report.results if r.status == "judged"]
    assert len(model.requests) == len(judged)
    assert any(r.claim.page_path != heron for r in judged)


def test_cli_checks_the_report_directory_before_the_first_judge_call(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = AppContext.create(settings)
    try:
        brain_id, _ = add_fixture_brain(ctx)
    finally:
        ctx.close()
    model = judge(*[label("supported")] * 60)
    scripted = IngestModel(chat_model=model, model_name="scripted", provider_id=1)
    monkeypatch.setattr(faithfulness, "get_ingest_model", lambda ctx: scripted)
    not_a_directory = tmp_path / "report.json"
    not_a_directory.write_text("{}")

    status = main(["--brain", brain_id, "--out", str(not_a_directory), "--seed", "3"])

    assert status == 1
    assert not model.requests
    assert "Cannot write a report to" in capsys.readouterr().err

    status = main(["--brain", brain_id, "--out", str(tmp_path / "evals"), "--seed", "3"])

    output = capsys.readouterr().out
    assert status == 0 and model.requests
    assert output.index("supported") < output.index("Report: ")
    reports = list((tmp_path / "evals").glob(f"faithfulness-{brain_id}-*.json"))
    assert len(reports) == 1
    assert FaithfulnessReport.model_validate_json(reports[0].read_text()).overall.judged > 0


def test_cli_fails_when_no_claim_could_be_judged(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The passage index of the Brain is empty, as after an update of its schema.
    ctx = AppContext.create(settings)
    try:
        brain_id, _ = add_fixture_brain(ctx, index=False)
    finally:
        ctx.close()
    model = judge()
    scripted = IngestModel(chat_model=model, model_name="scripted", provider_id=1)
    monkeypatch.setattr(faithfulness, "get_ingest_model", lambda ctx: scripted)

    status = main(["--brain", brain_id, "--out", str(tmp_path / "evals"), "--seed", "3"])

    captured = capsys.readouterr()
    assert status == 1
    assert not model.requests
    assert "Sync Brain" in captured.err
    assert "unresolved" in captured.out
    (path,) = (tmp_path / "evals").glob("*.json")
    report = FaithfulnessReport.model_validate_json(path.read_text())
    assert report.overall.judged == 0 and report.overall.unresolved == report.overall.claims > 0
    assert all(result.error for result in report.results)


def test_default_report_dir_is_git_ignored_from_any_working_directory(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("needs a git checkout of the repository")
    monkeypatch.chdir(tmp_path)

    directory = default_report_dir(settings)

    assert directory == REPO_ROOT / "private" / "evals"
    check = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", str(directory / "report.json")],
        check=False,
    )
    assert check.returncode == 0
