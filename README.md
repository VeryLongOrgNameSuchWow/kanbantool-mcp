# kanbantool-mcp

An MCP server that bridges Claude Code with the [Kanban Tool API v3](https://kanbantool.com/developer/api-v3).

> **Status:** Pre-alpha. Read tools land in M1, write tools in M2.

## Install

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

(Filled in as M1/M2 land.)

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
