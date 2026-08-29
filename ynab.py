"""Minimal YNAB API client. Standard library only.

Notes on the API as of 2026:
  * Base URL is https://api.ynab.com/v1
  * `/plans/{plan_id}` replaced `/budgets/{budget_id}` in v1.79.0. The old
    paths still work but are undocumented, so this uses /plans.
  * Rate limit is 200 requests per hour per token. Bulk-creating N
    transactions costs one request, so this is a non-issue in practice.
  * Money is in milliunits; dates are ISO 8601.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

BASE_URL = "https://api.ynab.com/v1"

# One POST carries many transactions. Keep batches modest so a request never
# runs into YNAB's 30-second processing timeout.
BATCH_SIZE = 500


class YnabError(RuntimeError):
    def __init__(self, status: int, name: str, detail: str):
        self.status, self.name, self.detail = status, name, detail
        super().__init__(f"YNAB API {status} {name}: {detail}")


@dataclass
class PushResult:
    created: list[str]
    duplicates: list[str]

    def __add__(self, other: "PushResult") -> "PushResult":
        return PushResult(
            self.created + other.created,
            self.duplicates + other.duplicates,
        )


class YnabClient:
    def __init__(self, token: str, plan_id: str, *, timeout: int = 60):
        self._token = token
        self._plan_id = plan_id
        self._timeout = timeout

    # ---- transactions -------------------------------------------------

    def create_transactions(self, payloads: list[dict]) -> PushResult:
        """Bulk-create. Anything whose import_id already exists is skipped.

        That skip is the entire deduplication story: re-running over an
        overlapping date range is safe and cheap.
        """
        result = PushResult([], [])
        for start in range(0, len(payloads), BATCH_SIZE):
            batch = payloads[start : start + BATCH_SIZE]
            data = self._request(
                "POST",
                f"/plans/{self._plan_id}/transactions",
                body={"transactions": batch},
            )
            result += _parse_push_response(data)
        return result

    def list_transactions(
        self,
        account_id: str,
        *,
        since: date | None = None,
        until: date | None = None,
    ) -> list[dict]:
        params: dict[str, str] = {}
        if since:
            params["since_date"] = since.isoformat()
        if until:
            params["until_date"] = until.isoformat()
        path = f"/plans/{self._plan_id}/accounts/{account_id}/transactions"
        data = self._request("GET", path, params=params)
        return [t for t in data.get("transactions", []) if not t.get("deleted")]

    # ---- discovery ----------------------------------------------------

    def list_plans(self) -> list[dict]:
        return self._request("GET", "/plans", unscoped=True).get("plans", [])

    def list_accounts(self) -> list[dict]:
        data = self._request("GET", f"/plans/{self._plan_id}/accounts")
        return [a for a in data.get("accounts", []) if not a.get("deleted")]

    # ---- plumbing -----------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict[str, str] | None = None,
        unscoped: bool = False,
    ) -> dict:
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if payload else {}),
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8")).get("data", {})
        except urllib.error.HTTPError as exc:
            raise _to_ynab_error(exc) from exc


def _to_ynab_error(exc: urllib.error.HTTPError) -> YnabError:
    try:
        error = json.loads(exc.read().decode("utf-8")).get("error", {})
    except Exception:
        error = {}
    return YnabError(
        exc.code,
        error.get("name", exc.reason or "unknown"),
        error.get("detail", "no detail provided"),
    )


def _parse_push_response(data: dict) -> PushResult:
    # The response has carried these fields at the top level of `data` since
    # v1.75.0, but older bulk responses nested them under `data.bulk`.
    payload = data.get("bulk", data)
    return PushResult(
        created=payload.get("transaction_ids") or [],
        duplicates=payload.get("duplicate_import_ids") or [],
    )
