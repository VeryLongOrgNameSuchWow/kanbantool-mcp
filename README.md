# kanbantool-mcp

An MCP server that bridges Claude Code with the [Kanban Tool API v3](https://kanbantool.com/developer/api-v3).

> **Status:** Pre-alpha, under active development. The tool surface and API are unstable. M1 (read tools) is in progress; M2 (write tools) and PyPI publishing are still ahead.

## Install

> The `uvx`-based install below will work once the package is published to PyPI ([#19](https://github.com/VeryLongNicknameSuchWow/kanbantool-mcp/issues/19)). Until then, run from a local checkout — see [Development](#development).

Add to your `mcp.json`:

```json
{
  "mcpServers": {
    "kanbantool": {
      "command": "uvx",
      "args": ["kanbantool-mcp"],
      "env": {
        "KANBANTOOL_DOMAIN": "your-account",
        "KANBANTOOL_API_TOKEN": "your-token"
      }
    }
  }
}
```

`KANBANTOOL_DOMAIN` is the prefix of your account URL (e.g. `acme` for `https://acme.kanbantool.com`). API tokens are issued from your Kanban Tool profile settings.

## Tools

Implemented today:

- `list_boards` — list boards visible to the authenticated user.
- `get_board` — fetch a single board (workflow stages, swimlanes, metadata) by ID.

More read tools (`search_tasks`, `get_task`, `recent_changes`, `list_subtasks`) and the M2 write tools are tracked on the issue board. README polish is tracked in [#16](https://github.com/VeryLongNicknameSuchWow/kanbantool-mcp/issues/16).

## Security

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities.

## Development

```sh
git clone https://github.com/VeryLongNicknameSuchWow/kanbantool-mcp.git
cd kanbantool-mcp
uv sync
uv run pytest
uv run ruff check .
uv run ty check
```

## License

[MIT](LICENSE)
