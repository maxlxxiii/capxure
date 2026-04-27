"""End-to-end smoke test: spawn `cap mcp` as a subprocess and round-trip a tool call."""

import json
import os
import subprocess
import threading


def _send(proc: subprocess.Popen, payload: dict) -> None:
    line = json.dumps(payload) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def _recv(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline()
    assert line, "MCP server returned no response"
    return json.loads(line.decode("utf-8"))


def _drain_stderr(proc: subprocess.Popen, sink: list[bytes]) -> threading.Thread:
    """Spawn a daemon thread that reads stderr until EOF into `sink`."""
    def _pump():
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                return
            sink.append(chunk)
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    return t


def _seed(db_path):
    """Create a minimal v3 db with one repo we can search for."""
    from capxure.db import Database
    with Database(db_path) as db:
        db.connection.execute(
            "INSERT INTO repos "
            "(github_id, owner, name, full_name, url, "
            " language, description, readme_content, stars, metadata) "
            "VALUES (1, 'octocat', 'hello-auth', 'octocat/hello-auth',"
            " 'https://github.com/octocat/hello-auth', 'Python',"
            " 'auth lib', 'authentication library for python', 100, '{}')"
        )


def test_cap_mcp_initializes_and_calls_tool(tmp_path):
    """Round-trip initialize -> tools/list -> tools/call(search_repos) over stdio."""
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    _seed(db_dir / "capxure.db")

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        ["cap", "mcp", "--data-dir", str(db_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stderr_chunks: list[bytes] = []
    drain_thread = _drain_stderr(proc, stderr_chunks)

    try:
        # 1. initialize
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0.0"},
            },
        })
        init_resp = _recv(proc)
        assert init_resp.get("id") == 1
        assert "result" in init_resp

        # 2. initialized notification (no response expected)
        _send(proc, {
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })

        # 3. tools/list — confirm all six are present
        _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        })
        list_resp = _recv(proc)
        tool_names = {t["name"] for t in list_resp["result"]["tools"]}
        assert tool_names == {
            "search_repos", "get_repo", "get_readme",
            "list_topics", "search_notes", "list_sources",
        }

        # 4. tools/call search_repos
        _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "search_repos",
                "arguments": {"query": "authentication"},
            },
        })
        call_resp = _recv(proc)
        assert call_resp.get("id") == 3
        result = call_resp["result"]
        # FastMCP returns BOTH a human-readable text content block and a
        # machine-readable structuredContent block for tools that return
        # structured data. The list payload lives at
        # result.structuredContent.result; the text block contains a JSON
        # serialization of the first list item only, which is why we read
        # the canonical list from structuredContent. (Plan Step 11.1
        # assumed content[0]["text"] would be the full list — current
        # FastMCP serializes it differently.)
        assert result.get("isError") is False
        assert "content" in result and len(result["content"]) >= 1
        assert result["content"][0]["type"] == "text"
        payload = result["structuredContent"]["result"]
        assert isinstance(payload, list)
        assert len(payload) >= 1
        assert payload[0]["name"] == "hello-auth"
        assert payload[0]["full_name"] == "octocat/hello-auth"
    except AssertionError:
        # Surface anything the server wrote to stderr — usually the
        # actual cause when the assertions above start failing.
        proc.stdin.close()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        drain_thread.join(timeout=2)
        stderr_text = b"".join(stderr_chunks).decode("utf-8", "replace")
        if stderr_text.strip():
            print("\n--- cap mcp stderr ---\n" + stderr_text + "\n----------------------")
        raise
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        drain_thread.join(timeout=2)
