"""
Utilities for when working with the ThoughtSpot APIs.

These are private because consumers of CS Tools codebase should ideally be
using the cs_tools.api.middlewares or consuming the cs_tools.api._rest_api_v1
directly.
"""
from __future__ import annotations

from typing import Any, Union
import copy
import json
import uuid

import httpx

UNDEFINED = object()
SYSTEM_USERS = {"system": "System User", "su": "Administrator Super-User", "tsadmin": "Administrator"}


def is_valid_guid(to_test: str) -> bool:
    """
    Determine if value is a valid UUID.

    Parameters
    ----------
    to_test : str
        value to test
    """
    try:
        guid = uuid.UUID(to_test)
    except ValueError:
        return False
    return str(guid) == to_test


def scrub_undefined_sentinel(inp: Any, *, null: Union[type[UNDEFINED], None]) -> Any:
    """
    Remove sentinel values from input parameters.

    httpx uses None as a meaningful value in some cases. We use the UNDEFINED object as
    a marker for a default value.
    """
    if isinstance(inp, dict):
        return {k: scrub_undefined_sentinel(v, null=null) for k, v in inp.items() if v is not null}

    if isinstance(inp, list):
        return [scrub_undefined_sentinel(v, null=null) for v in inp if v is not null]

    return inp


def obfuscate_sensitive_data(request_query: httpx.QueryParams) -> dict[str, Any]:
    """
    Remove sensitive data for logging. It's a poor man's logging.Filter.

    This is purely here to pop off the secrets.

    httpx.QueryParams.items() returns only the first specified parameter. If the user
    specifies the parameter multiple times, we'd have to switch to .multi_items().
    """
    SAFEWORDS = ("auth_token", "secret_key", "password", "access_token")

    # don't modify the actual keywords we want to build into the request
    secure = copy.deepcopy({k: v for k, v in request_query.items() if k not in ("file", "files")})

    for safe_word in SAFEWORDS:
        try:
            secure[safe_word] = "[secure]"
        except KeyError:
            pass

    return secure


def dumps(inp: Union[list[Any], type[UNDEFINED]]) -> Union[str, type[UNDEFINED]]:
    """
    json.dumps, but passthru our UNDEFINED sentinel.
    """
    if inp is UNDEFINED:
        return inp

    return json.dumps(inp)
