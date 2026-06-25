"""Adapters for 1ai-ecosystem service integration.

Each adapter wraps one ecosystem service. Pattern:
- REST services → httpx async client
- CLI tools → subprocess
- MCP services → MCP client (future)

Port map (from cf-router/apps.yaml):
  phonefarm-api : 8889    social-api : 8200    1proxy-api : 8000
  waha          : 3010    affiliate  : 3001    engage-mcp : 8766
  reach-api     : 8001    hub        : 9099    phonefarm  : 3080
"""
