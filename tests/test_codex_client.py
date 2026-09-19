"""JSON-RPC sidecar client for `codex app-server`.

Driven by a scripted fake process — the real protocol shapes here were taken
from `codex app-server generate-json-schema` and two live probe turns against
codex-cli 0.150.1, and are pinned in docs/260827-feat-CODEX_TRANSLATOR_PROVIDER.md.

Every failure mode in this file is one that must be loud: a missing binary, a
login we do not have, a failed turn, a turn that never completes. Silence on
any of them would look like a hung translation run.
"""

import json
import os
import tempfile
import threading

import pytest

from book_maker.codex_client import (
    UNWANTED_FEATURES,
    CodexAppServer,
    CodexError,
    CodexLoginRequired,
    CodexTurnFailed,
    CodexUnavailable,
)


class FakeProcess:
    """A scripted app-server: maps request methods to canned results."""

    def __init__(self, handlers, notifications_for=None):
        self.handlers = handlers
        self.notifications_for = notifications_for or {}
        self.sent = []
        self._lines = []
        self._ready = threading.Condition()
        self._closed = False
        self.stdin = self
        self.stdout = self
        self.stderr = None

    # -- stdin side --
    def write(self, payload):
        message = json.loads(payload)
        self.sent.append(message)
        method = message.get("method")
        handler = self.handlers.get(method)
        out = []
        if handler is not None:
            result = handler(message) if callable(handler) else handler
            if isinstance(result, dict) and "error" in result:
                out.append({"id": message["id"], "error": result["error"]})
            else:
                out.append({"id": message["id"], "result": result})
        for note in self.notifications_for.get(method, []):
            out.append(note(message) if callable(note) else note)
        with self._ready:
            self._lines.extend(json.dumps(o) for o in out)
            self._ready.notify_all()

    def flush(self):
        pass

    # -- stdout side --
    def __iter__(self):
        while True:
            with self._ready:
                while not self._lines and not self._closed:
                    self._ready.wait(timeout=2)
                if self._lines:
                    yield self._lines.pop(0) + "\n"
                    continue
                if self._closed:
                    return

    def close(self):
        with self._ready:
            self._closed = True
            self._ready.notify_all()

    def terminate(self):
        self.close()

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return None


INIT = {"userAgent": "x", "codexHome": "/tmp"}
THREAD = {"thread": {"id": "th-1"}}
# What a hardened build reports: every unwanted feature off and every
# configured server disabled — one of them dashed, because dashed names are
# the case that breaks if the override is ever quoted. (Discovery reads only
# the names, so serving the same reply to both spawns is fine.)
CONFIG = {
    "config": {
        "features": {name: False for name in UNWANTED_FEATURES},
        "mcp_servers": {
            "docs-search": {"url": "https://example.test/mcp", "enabled": False},
            "node-repl": {"command": "/bin/node-repl", "enabled": False},
        },
    }
}
MCP_STATUS = {
    "data": [
        {"name": "docs-search", "tools": {}},
        {"name": "node-repl", "tools": {}},
    ],
    "nextCursor": None,
}


def _turn_completed(status="completed", text="译文", error=None):
    def build(message):
        items = []
        if text is not None:
            items.append(
                {"type": "agentMessage", "phase": "final_answer", "text": text}
            )
        return {
            "method": "turn/completed",
            "params": {
                "threadId": "th-1",
                "turn": {
                    "id": "tu-1",
                    "items": items,
                    "status": status,
                    "error": error,
                },
            },
        }

    return build


def _server(handlers=None, notifications_for=None):
    handlers = {
        "initialize": INIT,
        "config/read": CONFIG,
        "mcpServerStatus/list": MCP_STATUS,
        "thread/start": THREAD,
        "turn/start": {"turn": {"id": "tu-1", "status": "inProgress"}},
        **(handlers or {}),
    }
    server = CodexAppServer()
    # start() spawns twice (discovery, then hardened), so the spawn callable
    # hands out a fresh scripted process per call. `server.fake` follows the
    # live one; `server.spawns` keeps them all for phase assertions. `fake`,
    # not `process`: assigning `process` would make start() a no-op.
    server.spawns = []

    def spawn(args):
        proc = FakeProcess(handlers, notifications_for)
        proc.args = list(args)
        server.spawns.append(proc)
        server.fake = proc
        return proc

    server._spawn = spawn
    return server


class TestStartup:
    def test_missing_binary_is_a_clear_error(self):
        def spawn(args):
            raise FileNotFoundError("codex")

        server = CodexAppServer(spawn=spawn)
        with pytest.raises(CodexUnavailable) as e:
            server.start()
        assert "codex" in str(e.value).lower()

    def test_initialize_sends_client_info(self):
        server = _server()
        server.start()
        try:
            init = next(m for m in server.fake.sent if m["method"] == "initialize")
            assert init["params"]["clientInfo"]["name"]
            assert init["params"]["clientInfo"]["version"]
        finally:
            server.close()

    def test_requests_carry_distinct_ids(self):
        server = _server({"account/rateLimits/read": {"rateLimits": {}}})
        server.start()
        try:
            server.request("account/rateLimits/read")
            ids = [m["id"] for m in server.fake.sent if "id" in m]
            assert ids and len(ids) == len(set(ids))
        finally:
            server.close()

    def test_a_request_with_no_reply_times_out_rather_than_hanging(self):
        server = _server()
        server.start()
        try:
            with pytest.raises(CodexError) as e:
                server.request("account/usage/read", timeout=0.4)
            assert "did not answer" in str(e.value)
        finally:
            server.close()


class TestRateLimits:
    LIMITS = {
        "rateLimits": {
            "primary": {
                "usedPercent": 42,
                "windowDurationMins": 300,
                "resetsAt": 1788055986,
            },
            "planType": "plus",
            "rateLimitReachedType": None,
        }
    }

    def test_reads_used_percent_and_reset(self):
        server = _server({"account/rateLimits/read": self.LIMITS})
        server.start()
        try:
            limits = server.rate_limits()
            assert limits.used_percent == 42
            assert limits.plan_type == "plus"
            assert limits.resets_at == 1788055986
        finally:
            server.close()

    def test_missing_rate_limits_is_not_fatal(self):
        server = _server({"account/rateLimits/read": {}})
        server.start()
        try:
            assert server.rate_limits() is None
        finally:
            server.close()


class TestLogin:
    def test_not_logged_in_points_at_the_codex_cli(self):
        server = _server(
            {
                "account/rateLimits/read": {
                    "error": {"code": -32000, "message": "not logged in"}
                }
            }
        )
        server.start()
        try:
            with pytest.raises(CodexLoginRequired, match="codex login"):
                server.ensure_logged_in()
        finally:
            server.close()


class TestThreadAndTurn:
    def test_start_thread_returns_the_id(self):
        server = _server()
        server.start()
        try:
            assert server.start_thread(model="gpt-5.6-sol") == "th-1"
        finally:
            server.close()

    def test_thread_start_is_non_agentic(self):
        """read-only + never-approve is what makes a turn behave like a completion."""
        server = _server()
        server.start()
        try:
            server.start_thread(model="m", base_instructions="be a translator")
            params = next(m for m in server.fake.sent if m["method"] == "thread/start")[
                "params"
            ]
            assert params["sandbox"] == "read-only"
            assert params["approvalPolicy"] == "never"
            assert params["baseInstructions"] == "be a translator"
        finally:
            server.close()

    def test_run_turn_returns_the_final_agent_message(self):
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            assert server.run_turn("th-1", "The dog barked.") == "译文"
        finally:
            server.close()

    def test_run_turn_passes_an_output_schema(self):
        schema = {"type": "object", "properties": {}}
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            server.run_turn("th-1", "text", output_schema=schema)
            params = next(m for m in server.fake.sent if m["method"] == "turn/start")[
                "params"
            ]
            assert params["outputSchema"] == schema
        finally:
            server.close()

    def test_failed_turn_raises_with_the_message(self):
        server = _server(
            notifications_for={
                "turn/start": [
                    _turn_completed(
                        status="failed", text=None, error={"message": "quota"}
                    )
                ]
            }
        )
        server.start()
        try:
            with pytest.raises(CodexTurnFailed) as e:
                server.run_turn("th-1", "text")
            assert "quota" in str(e.value)
        finally:
            server.close()

    def test_turn_with_no_agent_message_raises(self):
        server = _server(notifications_for={"turn/start": [_turn_completed(text=None)]})
        server.start()
        try:
            with pytest.raises(CodexTurnFailed):
                server.run_turn("th-1", "text")
        finally:
            server.close()

    def test_a_turn_that_never_completes_times_out(self):
        server = _server()  # no turn/completed notification ever arrives
        server.start()
        try:
            with pytest.raises(CodexTurnFailed) as e:
                server.run_turn("th-1", "text", timeout=0.5)
            assert "timed out" in str(e.value).lower()
        finally:
            server.close()

    def test_notifications_arriving_before_the_response_are_not_lost(self):
        """turn/completed can land before turn/start's own reply is processed."""
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            assert server.run_turn("th-1", "text") == "译文"
        finally:
            server.close()


class TestContextManager:
    def test_closes_the_process_on_exit(self):
        server = _server()
        with server:
            pass
        assert server.fake._closed


class TestRobustness:
    def test_notifications_do_not_grow_without_bound(self):
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            for _ in range(40):
                server.run_turn("th-1", "text")
            assert len(server._notifications) < 40
        finally:
            server.close()

    def test_a_failed_initialize_does_not_leave_the_child_running(self):
        proc = FakeProcess({})  # never answers initialize
        server = CodexAppServer(spawn=lambda args: proc)
        with pytest.raises(CodexError):
            server.start(init_timeout=0.3)
        assert proc._closed

    def test_close_is_idempotent(self):
        server = _server()
        server.start()
        server.close()
        server.close()  # must not raise


def _disabled_features(args):
    return {args[i + 1] for i, a in enumerate(args) if a == "--disable"}


def _config_overrides(args):
    return {args[i + 1] for i, a in enumerate(args) if a == "-c"}


class TestHardening:
    """Book text is untrusted input to an agent runtime.

    Capability removal is the only mitigation with a hard guarantee (model
    refusal is not one — measured), so the sidecar is spawned with every
    agent-facing feature disabled and every configured MCP server off, and
    both removals are verified before the first turn. Every check here fails
    loud: a silent half-hardened run is worse than no run.
    """

    def test_the_handshake_sends_initialized_before_anything_else(self):
        server = _server()
        server.start()
        try:
            for proc in server.spawns:
                methods = [m.get("method") for m in proc.sent]
                assert methods[0] == "initialize"
                assert methods[1] == "initialized"
                assert "id" not in proc.sent[1]  # a notification, not a request
        finally:
            server.close()

    def test_discovery_spawns_plain_and_is_closed_before_the_hardened_spawn(self):
        server = _server()
        server.start()
        try:
            assert len(server.spawns) == 2
            first, live = server.spawns
            assert first.args == []
            assert first._closed
            assert "config/read" in [m.get("method") for m in first.sent]
            assert live.args != []
        finally:
            server.close()

    def test_the_live_spawn_disables_every_unwanted_feature(self):
        server = _server()
        server.start()
        try:
            assert _disabled_features(server.fake.args) == set(UNWANTED_FEATURES)
        finally:
            server.close()

    def test_the_live_spawn_disables_every_configured_mcp_server(self):
        """Dashed names must go through unquoted — quoting replaces the table."""
        server = _server()
        server.start()
        try:
            assert _config_overrides(server.fake.args) == {
                "mcp_servers.docs-search.enabled=false",
                "mcp_servers.node-repl.enabled=false",
            }
        finally:
            server.close()

    def test_a_flag_this_build_does_not_know_is_dropped_loudly(self, capsys):
        """An unknown --disable kills the sidecar; only that flag may be dropped."""
        server = _server()
        inner = server._spawn

        def spawn(args):
            if "multi_agent" in args and len(server.spawns) == 1:
                with open(server._stderr_path, "w") as f:
                    f.write("Error: Unknown feature flag: multi_agent\n")
                proc = FakeProcess({})
                proc.args = list(args)
                proc.close()  # dies before serving, like the real sidecar
                server.spawns.append(proc)
                return proc
            return inner(args)

        server._spawn = spawn
        server.start()
        try:
            assert len(server.spawns) == 3
            kept = _disabled_features(server.fake.args)
            assert kept == set(UNWANTED_FEATURES) - {"multi_agent"}
            assert "multi_agent" in capsys.readouterr().err
        finally:
            server.close()

    def test_a_spawn_death_with_no_known_flag_named_is_fatal(self):
        server = _server()
        inner = server._spawn

        def spawn(args):
            if args:
                proc = FakeProcess({})
                proc.args = list(args)
                proc.close()
                server.spawns.append(proc)
                return proc
            return inner(args)

        server._spawn = spawn
        with pytest.raises(CodexError):
            server.start()

    def test_a_server_still_exposing_tools_after_hardening_raises(self):
        server = _server(
            {
                "mcpServerStatus/list": {
                    "data": [{"name": "node-repl", "tools": {"js": {}}}],
                    "nextCursor": None,
                }
            }
        )
        with pytest.raises(CodexError, match="node-repl"):
            server.start()
        for proc in server.spawns:  # no orphans, discovery included
            assert proc._closed

    def test_a_feature_that_did_not_turn_off_raises(self):
        features = {name: False for name in UNWANTED_FEATURES}
        features["shell_tool"] = True
        config = {"config": {**CONFIG["config"], "features": features}}
        server = _server({"config/read": config})
        with pytest.raises(CodexError, match="shell_tool"):
            server.start()
        for proc in server.spawns:
            assert proc._closed

    def test_a_server_override_that_did_not_take_raises(self):
        """Empty tools alone prove nothing — a slow server also shows none."""
        config = {
            "config": {
                "features": dict(CONFIG["config"]["features"]),
                "mcp_servers": {"docs-search": {"enabled": True}},
            }
        }
        server = _server({"config/read": config})
        with pytest.raises(CodexError, match="docs-search"):
            server.start()
        for proc in server.spawns:
            assert proc._closed

    def test_a_server_name_that_cannot_be_written_as_an_override_raises(self):
        config = {
            "config": {
                "features": dict(CONFIG["config"]["features"]),
                "mcp_servers": {"weird server!": {"enabled": True}},
            }
        }
        server = _server({"config/read": config})
        with pytest.raises(CodexError, match="weird server!"):
            server.start()
        for proc in server.spawns:
            assert proc._closed

    def test_threads_run_in_a_private_empty_directory(self):
        """cwd '.' ran book turns where the user launched bbm — with their
        project config and hook droppings. Threads get an empty dir instead."""
        server = _server()
        server.start()
        try:
            server.start_thread(model="m")
            params = next(
                m for m in server.fake.sent if m.get("method") == "thread/start"
            )["params"]
            assert params["cwd"] == server._work_dir
            assert os.path.isdir(params["cwd"])
            assert os.listdir(params["cwd"]) == []
        finally:
            server.close()

    def test_close_removes_the_private_directory(self):
        server = _server()
        server.start()
        run_dir = server._run_dir
        assert os.path.isdir(run_dir)
        server.close()
        assert not os.path.exists(run_dir)

    def test_a_failed_directory_setup_leaves_nothing_behind(self, monkeypatch):
        made = []
        real_mkdtemp = tempfile.mkdtemp

        def mkdtemp(*a, **kw):
            made.append(real_mkdtemp(*a, **kw))
            return made[-1]

        monkeypatch.setattr("book_maker.codex_client.tempfile.mkdtemp", mkdtemp)
        real_mkdir = os.mkdir

        def mkdir(path, *args):
            # Only the client's own work dir; mkdtemp uses os.mkdir too.
            if os.path.basename(path) == "work":
                raise OSError("disk full")
            return real_mkdir(path, *args)

        monkeypatch.setattr("book_maker.codex_client.os.mkdir", mkdir)
        server = _server()
        with pytest.raises(OSError):
            server.start()
        assert made and not os.path.exists(made[0])

    def test_concurrent_start_waits_for_the_verified_sidecar(self):
        """A start() racing another must not return an unverified process."""
        server = _server()
        release = threading.Event()
        inner = server._spawn

        def spawn(args):
            if not args:  # hold the discovery spawn until the race is on
                release.wait(timeout=5)
            return inner(args)

        server._spawn = spawn
        racers = [threading.Thread(target=server.start) for _ in range(2)]
        for t in racers:
            t.start()
        release.set()
        for t in racers:
            t.join(timeout=10)
        try:
            # Exactly one two-phase boot ran; the loser waited it out.
            assert len(server.spawns) == 2
            assert server.process is server.spawns[-1]
        finally:
            server.close()


class TestUserFacingErrors:
    """cli.py prints an error and stops only for `user_facing` exceptions.

    Everything else it re-raises, so an unmarked startup error reaches the
    user as a traceback with its carefully written instruction buried in it.
    """

    def test_the_startup_errors_are_marked_for_the_user(self):
        assert CodexUnavailable.user_facing is True
        assert CodexLoginRequired.user_facing is True
