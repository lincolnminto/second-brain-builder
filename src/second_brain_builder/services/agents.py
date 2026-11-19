"""What agents use: the setup instructions, and saving a Query answer.

Agents read a Brain through the MCP tools (``list_brains``, ``search``,
``read_page``) or a local clone, and write only through jobs: ``add_source`` and
``save_answer``.
"""

from __future__ import annotations

import shlex

from sqlalchemy import select

from second_brain_builder.domain import JobKind
from second_brain_builder.okf import cited_labels
from second_brain_builder.services._common import GITHUB_URL, brain_row, job_view, one_line_name
from second_brain_builder.services.brains import list_brains
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import InvalidInputError
from second_brain_builder.services.models import (
    AgentSetup,
    AnswerInput,
    BrainCloneCommand,
    JobView,
)
from second_brain_builder.services.payloads import AnswerSavePayload
from second_brain_builder.store import SourceRow

MCP_PATH = "/mcp"
SKILL_PATH = "/skills/second-brain/SKILL.md"
"""Where the app serves ``skills/second-brain/SKILL.md``."""
SKILL_DIR = "~/.claude/skills/second-brain"
CLONE_DIR = "~/second-brain"
"""Default parent directory of local Brain clones, as the skill expects."""
MCP_SERVER_NAME = "second-brain"
TOKEN_PLACEHOLDER = "<API_TOKEN>"  # noqa: S105 (a placeholder the user replaces)

MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 500
MAX_TAGS = 20
MAX_TAG_LENGTH = 50
MAX_ANSWER_CHARS = 200_000


def get_agent_setup(ctx: AppContext) -> AgentSetup:
    """The MCP URL, the skill install command, and a clone command for each Brain.

    Clones skip Git LFS downloads (``GIT_LFS_SKIP_SMUDGE=1``): agents read the
    Markdown, and the original Sources stay on GitHub until needed.
    """
    base = ctx.settings.public_url.rstrip("/")
    mcp_url = f"{base}{MCP_PATH}"
    skill_url = f"{base}{SKILL_PATH}"
    skill_file = f"{SKILL_DIR}/SKILL.md"
    clone_commands = [
        BrainCloneCommand(
            brain_id=brain.id,
            name=brain.name,
            command=(
                "GIT_LFS_SKIP_SMUDGE=1 git clone "
                f"{shlex.quote(f'{GITHUB_URL}/{brain.repo_full_name}.git')} "
                f"{CLONE_DIR}/{brain.slug}"
            ),
        )
        for brain in list_brains(ctx)
    ]
    return AgentSetup(
        mcp_url=mcp_url,
        mcp_add_command=(
            f"claude mcp add --transport http {MCP_SERVER_NAME} {shlex.quote(mcp_url)} "
            f'--header "Authorization: Bearer {TOKEN_PLACEHOLDER}"'
        ),
        skill_url=skill_url,
        skill_install_command=(
            f"mkdir -p {SKILL_DIR} && curl -fsSL {shlex.quote(skill_url)} -o {skill_file}"
        ),
        clone_commands=clone_commands,
    )


def save_answer(ctx: AppContext, brain_id: str, answer: AnswerInput) -> JobView:
    """Check an answer and enqueue ``answer.save``, which writes a synthesis Wiki Page.

    The checks here are the ones that need no clone: the title and body are not
    empty, every id in ``source_ids`` and every footnote label in the body is a
    Source of the Brain, and the body cites every id in ``source_ids``. The job
    runs the full OKF checks.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: A check failed. The message lists the problems.
    """
    title = one_line_name(answer.title, "title", MAX_TITLE_LENGTH)
    body = answer.body.strip()
    if not body:
        raise InvalidInputError("The answer body is empty.")
    if len(body) > MAX_ANSWER_CHARS:
        raise InvalidInputError(f"The answer body is longer than {MAX_ANSWER_CHARS} characters.")
    description = " ".join(answer.description.split())
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise InvalidInputError(
            f"The description is longer than {MAX_DESCRIPTION_LENGTH} characters."
        )
    tags = _tags(answer.tags)
    source_ids = list(dict.fromkeys(item.strip() for item in answer.source_ids if item.strip()))
    with ctx.read_sessions() as session:
        brain_row(session, brain_id)
        known = set(session.scalars(select(SourceRow.slug).where(SourceRow.brain_id == brain_id)))
    cited = cited_labels(body)
    problems: list[str] = []
    unknown = sorted((set(source_ids) | cited) - known)
    if unknown:
        problems.append(f"These are not Source ids of this Brain: {', '.join(unknown)}.")
    if not source_ids:
        problems.append("List the ids of the Sources that the answer cites in source_ids.")
    uncited = [item for item in source_ids if item not in cited]
    if uncited:
        problems.append(
            "The body does not cite these Sources; cite claims with [^source-id]: "
            f"{', '.join(uncited)}."
        )
    if problems:
        raise InvalidInputError(" ".join(problems))
    payload = AnswerSavePayload(
        brain_id=brain_id,
        title=title,
        body=body,
        source_ids=source_ids,
        description=description,
        tags=tags,
    )
    return job_view(ctx.jobs.enqueue(JobKind.ANSWER_SAVE, brain_id, payload.to_json()))


def _tags(values: list[str]) -> list[str]:
    tags = list(dict.fromkeys(" ".join(value.split()) for value in values if value.strip()))
    if len(tags) > MAX_TAGS:
        raise InvalidInputError(f"An answer can have at most {MAX_TAGS} tags.")
    if any(len(tag) > MAX_TAG_LENGTH for tag in tags):
        raise InvalidInputError(f"A tag can have at most {MAX_TAG_LENGTH} characters.")
    return tags
