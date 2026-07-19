"""FastMCP server exposing read/write tools for a Beancount ledger."""

from __future__ import annotations

import argparse
import datetime
import os
import re
from decimal import Decimal
from typing import Annotated, Any

import beanquery
from beancount import loader
from beancount.core import data
from beancount.parser import parser as bean_parser
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

mcp = FastMCP("beancount")

_ledger_path: str | None = None

ACCOUNT_RE = re.compile(r"^[A-Z][A-Za-z0-9\-]*(:[A-Z0-9][A-Za-z0-9\-]*)+$")


def get_ledger_path() -> str:
    path = _ledger_path or os.environ.get("BEANCOUNT_FILE")
    if not path:
        raise ValueError(
            "No ledger configured. Set the BEANCOUNT_FILE environment variable "
            "or pass the ledger path as a command-line argument."
        )
    if not os.path.exists(path):
        raise ValueError(f"Ledger file not found: {path}")
    return path


def _fmt(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_fmt(v) for v in value]
    return str(value)


def _format_errors(errors: list[Any]) -> list[dict[str, Any]]:
    out = []
    for err in errors:
        source = getattr(err, "source", None) or {}
        out.append(
            {
                "message": getattr(err, "message", str(err)),
                "file": source.get("filename"),
                "line": source.get("lineno"),
            }
        )
    return out


def _run_query(query: str) -> dict[str, Any]:
    conn = beanquery.connect(f"beancount:{get_ledger_path()}")
    cursor = conn.execute(query)
    columns = [col.name for col in cursor.description or []]
    rows = [[_fmt(value) for value in row] for row in cursor.fetchall()]
    return {"columns": columns, "rows": rows}


@mcp.tool()
def run_query(
    query: Annotated[
        str,
        Field(
            description=(
                "BQL (Beancount Query Language) query, e.g. "
                "\"SELECT account, sum(position) WHERE account ~ 'Expenses' GROUP BY account\""
            )
        ),
    ],
) -> dict[str, Any]:
    """Run a BQL query against the ledger and return columns and rows."""
    return _run_query(query)


@mcp.tool()
def list_accounts(
    prefix: Annotated[
        str,
        Field(
            description="Only return accounts starting with this prefix (e.g. 'Expenses')"
        ),
    ] = "",
) -> list[dict[str, Any]]:
    """List accounts in the ledger with their open/close dates and currencies."""
    entries, _, _ = loader.load_file(get_ledger_path())
    accounts: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, data.Open):
            accounts[entry.account] = {
                "account": entry.account,
                "open_date": entry.date.isoformat(),
                "close_date": None,
                "currencies": list(entry.currencies or []),
            }
        elif isinstance(entry, data.Close) and entry.account in accounts:
            accounts[entry.account]["close_date"] = entry.date.isoformat()
    result = [
        info for name, info in sorted(accounts.items()) if name.startswith(prefix)
    ]
    return result


@mcp.tool()
def get_balances(
    account_prefix: Annotated[
        str, Field(description="Restrict to accounts starting with this prefix")
    ] = "",
    at_date: Annotated[
        str | None,
        Field(description="Balances as of this date (YYYY-MM-DD, exclusive)"),
    ] = None,
) -> dict[str, Any]:
    """Get per-account balances, optionally filtered by account prefix and date."""
    conditions = []
    if account_prefix:
        safe = re.escape(account_prefix.replace("'", ""))
        conditions.append(f"account ~ '^{safe}'")
    if at_date:
        datetime.date.fromisoformat(at_date)
        conditions.append(f"date < {at_date}")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return _run_query(
        f"SELECT account, sum(position) AS balance{where} GROUP BY account ORDER BY account"
    )


@mcp.tool()
def search_transactions(
    text: Annotated[
        str | None,
        Field(description="Case-insensitive match against payee or narration"),
    ] = None,
    account: Annotated[
        str | None,
        Field(
            description="Match transactions with a posting to accounts matching this regex"
        ),
    ] = None,
    start_date: Annotated[
        str | None, Field(description="YYYY-MM-DD, inclusive")
    ] = None,
    end_date: Annotated[str | None, Field(description="YYYY-MM-DD, exclusive")] = None,
    limit: Annotated[
        int, Field(description="Maximum number of postings returned", ge=1, le=1000)
    ] = 100,
) -> dict[str, Any]:
    """Search transaction postings by payee/narration text, account, and date range."""
    conditions = []
    if text:
        safe = re.escape(text.replace("'", ""))
        conditions.append(f"(payee ~ '(?i){safe}' OR narration ~ '(?i){safe}')")
    if account:
        safe = account.replace("'", "")
        conditions.append(f"account ~ '{safe}'")
    if start_date:
        datetime.date.fromisoformat(start_date)
        conditions.append(f"date >= {start_date}")
    if end_date:
        datetime.date.fromisoformat(end_date)
        conditions.append(f"date < {end_date}")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return _run_query(
        f"SELECT date, flag, payee, narration, account, position, tags, links"
        f"{where} ORDER BY date DESC LIMIT {limit}"
    )


@mcp.tool()
def check_ledger() -> dict[str, Any]:
    """Validate the ledger (like bean-check) and return any errors."""
    entries, errors, _ = loader.load_file(get_ledger_path())
    return {
        "valid": not errors,
        "num_entries": len(entries),
        "errors": _format_errors(errors),
    }


class Posting(BaseModel):
    account: str = Field(
        description="Full account name, e.g. 'Expenses:Food:Restaurant'"
    )
    amount: str | None = Field(
        default=None,
        description="Decimal amount as a string, e.g. '-12.34'. Omit on at most one posting to let Beancount infer it.",
    )
    currency: str | None = Field(default=None, description="Currency code, e.g. 'JPY'")


def _format_transaction(
    date: str,
    flag: str,
    payee: str | None,
    narration: str,
    postings: list[Posting],
    tags: list[str],
    links: list[str],
) -> str:
    datetime.date.fromisoformat(date)
    if flag not in ("*", "!"):
        raise ValueError("flag must be '*' or '!'")
    if len(postings) < 2:
        raise ValueError("A transaction needs at least two postings")

    def quote(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    header = date + " " + flag
    if payee:
        header += " " + quote(payee)
    header += " " + quote(narration)
    for tag in tags:
        header += " #" + tag.removeprefix("#")
    for link in links:
        header += " ^" + link.removeprefix("^")

    lines = [header]
    for p in postings:
        if not ACCOUNT_RE.match(p.account):
            raise ValueError(f"Invalid account name: {p.account}")
        line = f"  {p.account}"
        if p.amount is not None:
            Decimal(p.amount)
            if not p.currency:
                raise ValueError(
                    f"Posting for {p.account} has an amount but no currency"
                )
            line += f"  {p.amount} {p.currency}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _append_to_ledger(entry_text: str) -> dict[str, Any]:
    _, parse_errors, _ = bean_parser.parse_string(entry_text)
    if parse_errors:
        raise ValueError(
            "Entry failed to parse; nothing was written: "
            + "; ".join(e.message for e in parse_errors)
        )

    path = get_ledger_path()
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    separator = (
        ""
        if content.endswith("\n\n") or not content
        else ("\n" if content.endswith("\n") else "\n\n")
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(separator + entry_text)

    _, errors, _ = loader.load_file(path)
    return {
        "written_to": path,
        "entry": entry_text,
        "ledger_errors_after_write": _format_errors(errors),
    }


@mcp.tool()
def add_transaction(
    date: Annotated[str, Field(description="Transaction date, YYYY-MM-DD")],
    narration: Annotated[str, Field(description="Transaction description")],
    postings: Annotated[
        list[Posting], Field(description="Postings; amounts must balance to zero")
    ],
    payee: Annotated[str | None, Field(description="Payee name")] = None,
    flag: Annotated[str, Field(description="'*' (cleared) or '!' (pending)")] = "*",
    tags: Annotated[list[str], Field(description="Tags without '#'")] = [],
    links: Annotated[list[str], Field(description="Links without '^'")] = [],
) -> dict[str, Any]:
    """Append a new transaction to the ledger file. The entry is parsed and validated before writing."""
    entry_text = _format_transaction(
        date, flag, payee, narration, postings, tags, links
    )
    return _append_to_ledger(entry_text)


@mcp.tool()
def append_entry(
    text: Annotated[
        str,
        Field(
            description=(
                "Raw Beancount directive(s) to append, e.g. a 'balance', 'open', or "
                "'note' directive, or a full transaction in Beancount syntax"
            )
        ),
    ],
) -> dict[str, Any]:
    """Append raw Beancount directive text to the ledger file after checking that it parses."""
    if not text.endswith("\n"):
        text += "\n"
    return _append_to_ledger(text)


def main() -> None:
    global _ledger_path
    argparser = argparse.ArgumentParser(description="MCP server for a Beancount ledger")
    argparser.add_argument(
        "ledger",
        nargs="?",
        default=None,
        help="Path to the main Beancount file (default: $BEANCOUNT_FILE)",
    )
    args = argparser.parse_args()
    _ledger_path = args.ledger
    get_ledger_path()  # fail fast if unset or missing
    mcp.run()


if __name__ == "__main__":
    main()
