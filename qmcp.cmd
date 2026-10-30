@echo off
rem Command-line bridge to the QSPICE MCP tools (see src\qspice_mcp\cli.py)
set PYTHONIOENCODING=utf-8
"%~dp0.venv\Scripts\python.exe" -m qspice_mcp.cli %*
