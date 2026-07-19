# beancount-mcp-server

MCP (Model Context Protocol) server for [Beancount](https://beancount.github.io/) ledgers.
Lets MCP clients (Claude Code, Claude Desktop, etc.) query a ledger with BQL and append new entries.

## Tools

| Tool | Description |
| --- | --- |
| `run_query` | Run an arbitrary BQL query |
| `list_accounts` | List accounts with open/close dates and currencies |
| `get_balances` | Per-account balances, filterable by prefix and date |
| `search_transactions` | Search postings by text, account, and date range |
| `check_ledger` | Validate the ledger (bean-check equivalent) |
| `add_transaction` | Append a new transaction (validated before writing) |
| `append_entry` | Append raw Beancount directive text (validated before writing) |

## Usage

The ledger path is taken from the first CLI argument, or from `$BEANCOUNT_FILE`.

```sh
uvx beancount-mcp-server /path/to/main.beancount
```

### Claude Code / Claude Desktop config

```json
{
  "mcpServers": {
    "beancount": {
      "command": "uvx",
      "args": ["beancount-mcp-server", "/path/to/main.beancount"]
    }
  }
}
```

### Docker

```sh
docker build -t beancount-mcp-server .
docker run -i --rm -v /path/to/ledger:/data beancount-mcp-server
```

The image expects the main file at `/data/main.beancount` (override via `BEANCOUNT_FILE`).

## Development

```sh
uv sync
uv run beancount-mcp-server /path/to/main.beancount
```
