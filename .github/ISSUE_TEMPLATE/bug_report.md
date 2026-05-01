---
name: Bug report
about: Something isn't working as documented
title: ""
labels: ["type:bug"]
---

## What happened

<!-- One paragraph: what you tried, what happened, why it surprised you. -->

## Reproduction

<!-- Minimum steps. Include the exact MCP tool call (or curl) and the
     response you got. DO NOT paste your KANBANTOOL_API_TOKEN — redact
     to "<TOKEN>". Domain (subdomain prefix) is fine. -->

```
KANBANTOOL_DOMAIN=<your-subdomain>
KANBANTOOL_API_TOKEN=<TOKEN>
```

```
> tool_name(arg=value)
```

## Expected

<!-- What did you expect to happen instead? -->

## Environment

- `kanbantool-mcp` version: <!-- `uvx kanbantool-mcp --version` or check the wheel METADATA -->
- Python version: <!-- `python --version` -->
- OS: <!-- macOS / Linux distro / Windows -->
- MCP client: <!-- Claude Code / Cursor / other -->

## Anything else
