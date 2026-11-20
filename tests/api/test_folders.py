"""Folder routes and the Folder filter of the page list and the graph, on a real Brain clone."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from .conftest import WEB

if TYPE_CHECKING:
    from .conftest import BrainFixture


def folders(client: TestClient, brain_id: str) -> dict[str, object]:
    """``GET /folders`` as nested dicts: ``{name: (source_count, children)}``."""

    def nested(node: dict[str, object]) -> dict[str, object]:
        children = node["children"]
        assert isinstance(children, list)
        return {child["name"]: (child["source_count"], nested(child)) for child in children}

    response = client.get(f"/api/brains/{brain_id}/folders")
    assert response.status_code == 200
    return nested(response.json())


def test_folder_changes_enqueue_jobs(client: TestClient, brain: BrainFixture) -> None:
    base = f"/api/brains/{brain.id}/folders"

    created = client.post(base, json={"folder": "Work/Client Notes"}, headers=WEB)
    renamed = client.post(
        f"{base}/rename", json={"folder": "nature", "new_folder": "coast"}, headers=WEB
    )

    assert created.status_code == 202
    assert (created.json()["kind"], created.json()["payload"]["folder"]) == (
        "folder.create",
        "work/client-notes",
    )
    assert renamed.status_code == 202
    assert renamed.json()["kind"] == "folder.rename"
    assert renamed.json()["payload"]["new_folder"] == "coast"


def test_empty_folders_are_listed_and_deleted(client: TestClient, brain: BrainFixture) -> None:
    (brain.root / "sources" / "archive").mkdir()
    (brain.root / "sources" / "archive" / ".gitkeep").write_text("")
    base = f"/api/brains/{brain.id}/folders"

    assert folders(client, brain.id) == {"archive": (0, {}), "nature": (2, {})}
    deleted = client.delete(base, params={"folder": "archive"}, headers=WEB)
    refused = client.delete(base, params={"folder": "nature"}, headers=WEB)

    assert deleted.status_code == 202
    assert deleted.json()["kind"] == "folder.delete"
    assert refused.status_code == 409
    assert refused.json()["code"] == "conflict"


@pytest.mark.parametrize("folder", ["../outside", "nature/../..", ".git"])
def test_traversal_and_dot_segments_are_rejected(
    client: TestClient, brain: BrainFixture, folder: str
) -> None:
    base = f"/api/brains/{brain.id}"

    responses = [
        client.post(f"{base}/folders", json={"folder": folder}, headers=WEB),
        client.post(
            f"{base}/folders/rename", json={"folder": "nature", "new_folder": folder}, headers=WEB
        ),
        client.delete(f"{base}/folders", params={"folder": folder}, headers=WEB),
        client.get(f"{base}/pages", params={"folder": folder}),
        client.get(f"{base}/graph", params={"folder": folder}),
    ]

    assert [response.status_code for response in responses] == [422] * 5


def test_pages_and_graph_take_a_folder_filter(client: TestClient, brain: BrainFixture) -> None:
    other = brain.root / "sources" / "elsewhere" / "atlas"
    other.mkdir(parents=True)
    (other / "atlas.md").write_text(
        "---\ntype: source\ntitle: Atlas\nresource: ./atlas.txt\n"
        'generated: {by: "process:sbb-conversion", at: "2026-11-18T12:00:00+00:00"}\n'
        "---\n\n# Atlas\n"
    )
    base = f"/api/brains/{brain.id}"

    pages = client.get(f"{base}/pages", params={"folder": "elsewhere"})
    graph = client.get(f"{base}/graph", params={"folder": "nature"})
    everything = client.get(f"{base}/pages")

    assert pages.status_code == 200
    assert [page["path"] for page in pages.json()] == ["sources/elsewhere/atlas/atlas.md"]
    assert graph.status_code == 200
    nodes = {node["path"] for node in graph.json()["nodes"]}
    assert "sources/elsewhere/atlas/atlas.md" not in nodes
    assert "wiki/entities/sea-star.md" in nodes
    assert nodes < {page["path"] for page in everything.json()}
