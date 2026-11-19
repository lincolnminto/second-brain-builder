"""Export the HTTP contract without opening the user's database or credentials."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet
from pydantic import SecretStr

from second_brain_builder.api.app import create_app
from second_brain_builder.config import Settings
from second_brain_builder.services import AppContext


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export_openapi.py OUTPUT")
    output = Path(sys.argv[1])
    with TemporaryDirectory(prefix="sbb-openapi-") as directory:
        root = Path(directory)
        settings = Settings(
            _env_prefix="SBB_OPENAPI_EXPORT_UNUSED_",
            data_dir=root / "data",
            brains_dir=root / "brains",
            models_dir=root / "models",
            secret_key=SecretStr(Fernet.generate_key().decode()),
        )
        context = AppContext.create(settings)
        try:
            schema = create_app(context=context).openapi()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        finally:
            context.close()


if __name__ == "__main__":
    main()
