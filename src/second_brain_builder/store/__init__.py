"""Database models, migrations, credential encryption, the job queue, and job events.

Typical setup in the ``app`` and the ``worker``::

    engine = make_engine(settings)
    migrate(engine)
    sessions = make_session_factory(engine)
    crypto = Crypto.load_or_create(settings)
    jobs = JobQueue(engine)
"""

from second_brain_builder.store.crypto import Crypto, DecryptionError, SecretKeyError
from second_brain_builder.store.db import (
    BUSY_TIMEOUT_MS,
    immediate_transactions,
    make_engine,
    make_session_factory,
    sqlite_engine,
)
from second_brain_builder.store.jobs import (
    DEFAULT_MAX_ATTEMPTS,
    STATUS_EVENT,
    Job,
    JobEvent,
    JobNotFoundError,
    JobQueue,
    JobStateError,
)
from second_brain_builder.store.migration import MigrationError, migrate
from second_brain_builder.store.models import (
    ApiTokenRow,
    Base,
    BrainRow,
    CredentialRow,
    GitHubAccountRow,
    JobEventRow,
    JobRow,
    ProviderRow,
    SettingRow,
    SourceRow,
    TaskSettingRow,
)
from second_brain_builder.store.types import utc_now

__all__ = [
    "BUSY_TIMEOUT_MS",
    "DEFAULT_MAX_ATTEMPTS",
    "STATUS_EVENT",
    "ApiTokenRow",
    "Base",
    "BrainRow",
    "CredentialRow",
    "Crypto",
    "DecryptionError",
    "GitHubAccountRow",
    "Job",
    "JobEvent",
    "JobEventRow",
    "JobNotFoundError",
    "JobQueue",
    "JobRow",
    "JobStateError",
    "MigrationError",
    "ProviderRow",
    "SecretKeyError",
    "SettingRow",
    "SourceRow",
    "TaskSettingRow",
    "immediate_transactions",
    "make_engine",
    "make_session_factory",
    "migrate",
    "sqlite_engine",
    "utc_now",
]
