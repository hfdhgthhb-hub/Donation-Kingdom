#!/usr/bin/env python3
"""Create a Roblox Model package from an exported .rbxm using Open Cloud."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

CREATE_ASSET_URL = "https://apis.roblox.com/assets/v1/assets"
OPERATION_BASE_URL = "https://apis.roblox.com/assets/v1/operations"
MAX_UPLOAD_BYTES = 20_000_000  # Roblox documents the Create Asset limit as 20 MB.


def require_positive_decimal(value: str, label: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError(f"{label} must be a positive numeric ID.")
    return value


def parse_operation_id(payload: dict[str, Any]) -> str:
    path = payload.get("path")
    if not isinstance(path, str) or not path.startswith("operations/"):
        raise ValueError(
            "Roblox did not return the expected operation path. "
            f"Response keys: {sorted(payload.keys())}"
        )
    operation_id = path.rsplit("/", 1)[-1]
    if not operation_id:
        raise ValueError("Roblox returned an empty operation ID.")
    return operation_id


def parse_asset_id(operation: dict[str, Any]) -> str:
    response = operation.get("response")
    if not isinstance(response, dict):
        error = operation.get("error") or operation.get("status") or operation
        raise RuntimeError(f"Roblox finished the operation without an asset response: {error}")

    asset_id = response.get("assetId")
    if asset_id is None:
        asset_path = response.get("path")
        if isinstance(asset_path, str) and asset_path.startswith("assets/"):
            asset_id = asset_path.rsplit("/", 1)[-1]

    if asset_id is None or not re.fullmatch(r"[1-9][0-9]*", str(asset_id)):
        raise RuntimeError(
            "Roblox marked the operation complete but did not return a valid assetId. "
            f"Response: {json.dumps(response, ensure_ascii=False)}"
        )
    return str(asset_id)


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: tuple[int, int] = (20, 90),
    **kwargs: Any,
) -> requests.Response:
    """Retry only transient HTTP responses and network errors.

    This helper is used for GET polling. The POST create request is deliberately
    not retried: a timed-out POST could have created a package already.
    """
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = session.request(
                method, url, headers=headers, timeout=timeout, **kwargs
            )
            if response.status_code not in (429, 500, 502, 503, 504):
                return response
            retry_after = response.headers.get("Retry-After")
            wait_seconds = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 20)
            last_error = RuntimeError(
                f"Temporary Roblox HTTP {response.status_code}: {response.text[:500]}"
            )
        except requests.RequestException as exc:
            last_error = exc
            wait_seconds = min(2**attempt, 20)

        if attempt < 4:
            time.sleep(wait_seconds)

    raise RuntimeError(f"Roblox request failed after retries: {last_error}")


def create_and_wait(
    model_path: Path,
    api_key: str,
    creator_id: str,
    creator_type: str,
    display_name: str,
    description: str,
    *,
    session: requests.Session | None = None,
    poll_interval: float = 4.0,
    max_wait_seconds: int = 720,
) -> str:
    if not model_path.is_file():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    size = model_path.stat().st_size
    if size <= 0:
        raise ValueError("The generated .rbxm file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Generated .rbxm is {size} bytes; Roblox Create Asset currently allows up to "
            f"20 MB per upload ({MAX_UPLOAD_BYTES} bytes)."
        )

    api_key = api_key.strip()
    if not api_key:
        raise ValueError("ROBLOX_API_KEY secret is missing.")

    creator_id = require_positive_decimal(creator_id, "Creator ID")
    creator_type = creator_type.strip().lower()
    if creator_type not in ("user", "group"):
        raise ValueError("Creator type must be either 'user' or 'group'.")
    creator_field = "userId" if creator_type == "user" else "groupId"
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("Asset display name cannot be empty.")
    metadata = {
        "assetType": "Model",
        "displayName": display_name,
        "description": description.strip() or "Map-only model extracted from a Roblox place.",
        "creationContext": {"creator": {creator_field: creator_id}},
    }
    headers = {"x-api-key": api_key}
    http = session or requests.Session()

    print(f"Uploading {model_path.name} ({size} bytes) as a Roblox Model package...")
    # Do not retry this POST automatically; an ambiguous timeout can otherwise
    # create duplicate packages. The API's operation ID is the idempotent path
    # for tracking the one request after Roblox accepts it.
    try:
        with model_path.open("rb") as model_file:
            response = http.post(
                CREATE_ASSET_URL,
                headers=headers,
                data={"request": json.dumps(metadata, separators=(",", ":"))},
                files={
                    "fileContent": (
                        model_path.name,
                        model_file,
                        "model/x-rbxm",
                    )
                },
                timeout=(30, 300),
            )
    except requests.RequestException as exc:
        raise RuntimeError(
            "The upload connection failed. The POST was not retried because Roblox may "
            "have accepted it before the connection failed. Check Creator Hub before retrying."
        ) from exc

    if not response.ok:
        raise RuntimeError(
            f"Roblox Create Asset returned HTTP {response.status_code}: {response.text[:1500]}"
        )

    try:
        create_payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Roblox returned non-JSON from Create Asset: {response.text[:1000]}"
        ) from exc

    operation_id = parse_operation_id(create_payload)
    operation_url = f"{OPERATION_BASE_URL}/{operation_id}"
    print("Roblox accepted the request. Waiting for asset processing...")

    deadline = time.monotonic() + max_wait_seconds
    attempt = 0
    while time.monotonic() < deadline:
        poll_response = request_with_retry(
            http,
            "GET",
            operation_url,
            headers=headers,
            timeout=(20, 60),
        )
        if not poll_response.ok:
            raise RuntimeError(
                f"Roblox operation check returned HTTP {poll_response.status_code}: "
                f"{poll_response.text[:1500]}"
            )

        try:
            operation = poll_response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Roblox returned non-JSON while checking operation: {poll_response.text[:1000]}"
            ) from exc

        if operation.get("done") is True:
            asset_id = parse_asset_id(operation)
            print(f"Roblox Model Asset ID: {asset_id}")
            return asset_id

        if operation.get("error"):
            raise RuntimeError(f"Roblox asset operation failed: {operation['error']}")

        attempt += 1
        time.sleep(min(poll_interval + min(attempt, 5) * 0.5, 8.0))

    raise TimeoutError(
        "Timed out waiting for Roblox asset processing. The operation may still finish; "
        f"check operation ID {operation_id} in the workflow log before uploading again."
    )


def main() -> int:
    model_path = Path(os.environ.get("MODEL_PATH", "DonationKingdom_Map.rbxm"))
    report_path = Path(os.environ.get("REPORT_PATH", "ExportReport.txt"))
    asset_id_path = Path("asset_id.txt")

    try:
        asset_id = create_and_wait(
            model_path=model_path,
            api_key=os.environ.get("ROBLOX_API_KEY", ""),
            creator_id=os.environ.get("CREATOR_ID", ""),
            creator_type=os.environ.get("CREATOR_TYPE", "user"),
            display_name=os.environ.get("ASSET_NAME", "Donation Kingdom Map"),
            description=os.environ.get(
                "ASSET_DESCRIPTION",
                "Map-only extraction from Donation Kingdom.rbxl. Scripts and UI removed.",
            ),
        )
        asset_id_path.write_text(asset_id + "\n", encoding="utf-8")
        print(f"Asset ID saved to {asset_id_path}")
        if report_path.exists():
            print(f"Exporter report: {report_path}")
        return 0
    except Exception as exc:  # Print a useful workflow failure without exposing secrets.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
