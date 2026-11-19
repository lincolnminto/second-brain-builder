"""Pages, raw files, and Source downloads of a real Brain clone, and path checks on them."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from second_brain_builder.ingest.progress import document_progress, progress_path, write_progress
from second_brain_builder.services import (
    ProviderInput,
    ProviderKind,
    TaskMode,
    TaskSettingInput,
    TaskSettingsInput,
    create_provider,
    put_task_settings,
)

from .conftest import WEB

if TYPE_CHECKING:
    from .conftest import BrainFixture

OUTSIDE_PAGE = "---\ntype: entity\ntitle: Outside\n---\n\nText outside the Brain.\n"
OUTSIDE_TEXT = "Text outside the Brain"


@pytest.mark.parametrize("state", ["missing", "invalid", "partial", "complete", "symlink"])
def test_source_list_and_get_include_nullable_part_counts(client, brain, ctx, state):
    from pathlib import PurePosixPath

    slug = "tide-pools"
    path = progress_path(brain.root, slug)
    expected = (None, None)
    if state != "missing":
        path.parent.mkdir(parents=True, exist_ok=True)
        if state == "invalid":
            path.write_text("parts_done: [broken yaml")
        elif state == "symlink":
            path.symlink_to(brain.root / ".brain/brain.yaml")
        else:
            progress = document_progress(
                brain.root,
                PurePosixPath("sources/nature/tide-pools/tide-pools.md"),
                64000,
                ctx.clock(),
            ).model_copy(
                update={
                    "parts_done": 1 if state == "partial" else 2,
                    "parts_total": 2,
                    "status": state,
                }
            )
            write_progress(brain.root, slug, progress)
            expected = (progress.parts_done, progress.parts_total)
    listing = client.get(f"/api/brains/{brain.id}/sources")
    detail = client.get(f"/api/brains/{brain.id}/sources/{slug}")
    assert listing.status_code == detail.status_code == 200
    source = next(item for item in listing.json() if item["slug"] == slug)
    for view in (source, detail.json()["source"]):
        assert (view["parts_done"], view["parts_total"]) == expected


def test_ingest_estimate_returns_parts_and_tokens(client, brain, ctx):
    from pathlib import PurePosixPath

    from second_brain_builder.ingest.progress import read_progress

    response = client.get(f"/api/brains/{brain.id}/sources/tide-pools/ingest-estimate")
    assert response.status_code == 200
    assert response.json() == {
        "parts_total": 1,
        "parts_done": 0,
        "estimated_tokens": 96000,
        "mode": "sequential",
        "map_parts_done": 0,
        "reduce_groups_done": 0,
        "reduce_groups_total": 0,
        "map_estimated_tokens": 0,
        "reduce_estimated_tokens": 0,
    }
    progress = document_progress(
        brain.root,
        PurePosixPath("sources/nature/tide-pools/tide-pools.md"),
        ctx.settings.ingest_part_tokens * 4,
        ctx.clock(),
    ).model_copy(update={"parts_done": 1, "status": "complete"})
    write_progress(brain.root, "tide-pools", progress)
    response = client.get(f"/api/brains/{brain.id}/sources/tide-pools/ingest-estimate")
    assert response.json() == {
        "parts_total": 1,
        "parts_done": 1,
        "estimated_tokens": 0,
        "mode": "sequential",
        "map_parts_done": 0,
        "reduce_groups_done": 0,
        "reduce_groups_total": 0,
        "map_estimated_tokens": 0,
        "reduce_estimated_tokens": 0,
    }
    response = client.get(
        f"/api/brains/{brain.id}/sources/tide-pools/ingest-estimate", params={"resume": False}
    )
    assert response.status_code == 200
    assert response.json() == {
        "parts_total": 1,
        "parts_done": 0,
        "estimated_tokens": 96000,
        "mode": "sequential",
        "map_parts_done": 0,
        "reduce_groups_done": 0,
        "reduce_groups_total": 0,
        "map_estimated_tokens": 0,
        "reduce_estimated_tokens": 0,
    }
    assert read_progress(brain.root, "tide-pools") == progress


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"resume": False, "max_tokens": 2000},
        {"mode": "map_reduce"},
        {"mode": "sequential"},
    ],
)
def test_ingest_accepts_optional_run_options(client, brain, ctx, body):
    provider = create_provider(
        ctx,
        ProviderInput(name="Synthetic", kind=ProviderKind.OPENAI, base_url="http://localhost/v1"),
    )
    put_task_settings(
        ctx,
        TaskSettingsInput(
            ingest=TaskSettingInput(
                mode=TaskMode.PROVIDER, provider_id=provider.id, model="test-model"
            )
        ),
    )
    response = client.post(
        f"/api/brains/{brain.id}/sources/tide-pools/ingest", headers=WEB, json=body
    )
    assert response.status_code == 202
    payload = response.json()["payload"]
    assert payload["resume"] == (body or {}).get("resume", True)
    assert payload["max_tokens"] == (body or {}).get("max_tokens", ctx.settings.ingest_max_tokens)
    assert payload["mode"] == (body or {}).get("mode", "auto")


def test_ingest_rejects_unknown_mode(client, brain):
    response = client.post(
        f"/api/brains/{brain.id}/sources/tide-pools/ingest", headers=WEB, json={"mode": "parallel"}
    )
    assert response.status_code == 422


def test_ingest_rejects_nonpositive_budget(client, brain):
    response = client.post(
        f"/api/brains/{brain.id}/sources/tide-pools/ingest", headers=WEB, json={"max_tokens": 0}
    )
    assert response.status_code == 422


def test_page_is_read_from_the_clone(client: TestClient, brain: BrainFixture) -> None:
    response = client.get(f"/api/brains/{brain.id}/pages/wiki/entities/sea-star.md")

    assert response.status_code == 200
    page = response.json()
    assert (page["path"], page["type"], page["title"]) == (
        "wiki/entities/sea-star.md",
        "entity",
        "Ochre Sea Star",
    )
    assert [source["id"] for source in page["sources"]] == ["tide-pools"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("area", ["pages", "files"])
@pytest.mark.parametrize(
    "path",
    [
        "..%2F..%2Foutside.md",
        "%2e%2e/%2e%2e/outside.md",
        "wiki/..%2F..%2F..%2Foutside.md",
        "wiki%5C..%5C..%5Coutside.md",
        ".git/config",
        "%2Egit/config",
        "wiki/entities/sea-star.md%00",
    ],
)
def test_paths_that_leave_the_brain_are_rejected(
    client: TestClient, brain: BrainFixture, area: str, path: str
) -> None:
    (brain.root.parent / "outside.md").write_text(OUTSIDE_PAGE)

    response = client.get(f"/api/brains/{brain.id}/{area}/{path}")

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"
    assert OUTSIDE_TEXT not in response.text


def test_host_absolute_path_stays_inside_the_brain(client: TestClient, brain: BrainFixture) -> None:
    outside = brain.root.parent / "outside.md"
    outside.write_text(OUTSIDE_PAGE)

    response = client.get(f"/api/brains/{brain.id}/files/{outside.as_posix()}")

    assert response.status_code == 404
    assert OUTSIDE_TEXT not in response.text


@pytest.mark.parametrize("area", ["pages", "files"])
def test_symbolic_link_out_of_the_brain_is_rejected(
    client: TestClient, brain: BrainFixture, area: str
) -> None:
    outside = brain.root.parent / "outside.md"
    outside.write_text(OUTSIDE_PAGE)
    (brain.root / "wiki" / "entities" / "leak.md").symlink_to(outside)

    response = client.get(f"/api/brains/{brain.id}/{area}/wiki/entities/leak.md")

    assert response.status_code == 422
    assert OUTSIDE_TEXT not in response.text


def test_asset_that_can_run_scripts_is_sent_as_an_attachment(
    client: TestClient, brain: BrainFixture
) -> None:
    response = client.get(
        f"/api/brains/{brain.id}/files/sources/nature/coastal-birds/assets/heron.svg"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_original_download_has_the_original_filename(
    client: TestClient, brain: BrainFixture
) -> None:
    source_file = brain.root / "sources" / "nature" / "tide-pools" / "tide-pools.txt"

    response = client.get(f"/api/brains/{brain.id}/sources/tide-pools/original")

    assert response.status_code == 200
    assert response.content == source_file.read_bytes()
    assert response.headers["content-disposition"] == 'inline; filename="tide-pools.txt"'


def test_source_detail_lists_the_citing_pages(client: TestClient, brain: BrainFixture) -> None:
    response = client.get(f"/api/brains/{brain.id}/sources/tide-pools")

    assert response.status_code == 200
    detail = response.json()
    assert detail["source"]["canonical_path"] == "sources/nature/tide-pools/tide-pools.md"
    assert "wiki/entities/sea-star.md" in [page["path"] for page in detail["citing_pages"]]


def test_search_and_type_filters_use_repeated_query_parameters(
    client: TestClient, brain: BrainFixture
) -> None:
    hits = client.get(f"/api/brains/{brain.id}/search", params={"q": "sea star"})
    entities = client.get(f"/api/brains/{brain.id}/pages", params={"type": "entity"})
    two_types = client.get(
        f"/api/brains/{brain.id}/pages", params=[("type", "entity"), ("type", "concept")]
    )

    assert hits.status_code == 200
    assert hits.json()[0]["path"] == "wiki/entities/sea-star.md"
    assert {page["type"] for page in entities.json()} == {"entity"}
    assert {page["type"] for page in two_types.json()} == {"entity", "concept"}
