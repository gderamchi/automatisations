from __future__ import annotations

import os
import re
from typing import Any


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
VALUE_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def substitute_env(value: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        return os.getenv(match.group(1), "")

    return ENV_PATTERN.sub(replacer, value)


def render_value_template(value: str, context: dict[str, Any]) -> str:
    def replacer(match: re.Match[str]) -> str:
        return str(context.get(match.group(1), ""))

    return VALUE_PATTERN.sub(replacer, value)
