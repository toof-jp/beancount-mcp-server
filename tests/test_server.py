import pytest

from beancount_mcp_server import server

LEDGER = """\
option "operating_currency" "JPY"

2026-01-01 open Assets:Cash JPY
2026-01-01 open Expenses:Food JPY
2026-01-01 open Income:Salary JPY

2026-07-01 * "Employer" "Salary"
  Assets:Cash  300000 JPY
  Income:Salary

2026-07-02 * "Ramen Shop" "Lunch"
  Expenses:Food  1200 JPY
  Assets:Cash
"""


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "main.beancount"
    path.write_text(LEDGER)
    monkeypatch.setenv("BEANCOUNT_FILE", str(path))
    return path


def test_check_ledger(ledger):
    result = server.check_ledger()
    assert result["valid"] is True
    assert result["num_entries"] == 5
    assert result["errors"] == []


def test_run_query(ledger):
    result = server.run_query(
        "SELECT account, sum(position) GROUP BY account ORDER BY account"
    )
    assert result["columns"] == ["account", "sum(position)"]
    assert ["Expenses:Food", "(1200 JPY)"] in result["rows"]


def test_list_accounts(ledger):
    accounts = server.list_accounts(prefix="Expenses")
    assert accounts == [
        {
            "account": "Expenses:Food",
            "open_date": "2026-01-01",
            "close_date": None,
            "currencies": ["JPY"],
        }
    ]


def test_get_balances(ledger):
    result = server.get_balances(account_prefix="Assets", at_date="2026-07-02")
    assert result["rows"] == [["Assets:Cash", "(300000 JPY)"]]


def test_search_transactions(ledger):
    result = server.search_transactions(text="ramen")
    accounts = [row[result["columns"].index("account")] for row in result["rows"]]
    assert set(accounts) == {"Expenses:Food", "Assets:Cash"}


def test_add_transaction(ledger):
    result = server.add_transaction(
        date="2026-07-19",
        narration="Coffee",
        payee="Cafe",
        postings=[
            server.Posting(account="Expenses:Food", amount="500", currency="JPY"),
            server.Posting(account="Assets:Cash"),
        ],
        tags=["coffee"],
    )
    assert result["ledger_errors_after_write"] == []
    assert '2026-07-19 * "Cafe" "Coffee" #coffee' in ledger.read_text()
    assert server.check_ledger()["num_entries"] == 6


def test_add_transaction_rejects_invalid_account(ledger):
    with pytest.raises(ValueError, match="Invalid account name"):
        server.add_transaction(
            date="2026-07-19",
            narration="Bad",
            postings=[
                server.Posting(account="not-an-account", amount="1", currency="JPY"),
                server.Posting(account="Assets:Cash"),
            ],
        )
    assert "Bad" not in ledger.read_text()


def test_append_entry(ledger):
    result = server.append_entry("2026-07-03 balance Assets:Cash  298800 JPY")
    assert result["ledger_errors_after_write"] == []
    assert "balance Assets:Cash" in ledger.read_text()


def test_append_entry_rejects_syntax_error(ledger):
    before = ledger.read_text()
    with pytest.raises(ValueError, match="failed to parse"):
        server.append_entry('2026-07-21 * broken "x"')
    assert ledger.read_text() == before
