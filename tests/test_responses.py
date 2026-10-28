"""lib/responses.py: the Responses API that Codex speaks, translated to Chat Completions."""

import http.server
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request

from helpers import REPO, FakeServer

sys.path.insert(0, str(REPO / "lib"))
import responses  # noqa: E402

TOOLS = [
    {"type": "function", "name": "shell", "description": "Runs a command.",
     "parameters": {"type": "object", "properties": {"command": {"type": "array"}}}},
    {"type": "custom", "name": "apply_patch", "description": "Edits files.",
     "format": {"type": "grammar", "syntax": "lark", "definition": "start: patch"}},
    {"type": "web_search"},
    {"type": "namespace", "name": "mcp__docs", "description": "Docs server.", "tools": [
        {"type": "function", "name": "search", "description": "Searches.", "parameters": {"type": "object"}}]},
]


def sse(*chunks):
    """A chat completion stream as mlx_lm.server sends it, with a keepalive first."""
    body = ": keepalive 512/1024\n\n" + "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode(), {"Content-Type": "text/event-stream"}


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def finish(reason, **fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": reason}]}


USAGE = {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 7, "total_tokens": 37}}


class ToChatTest(unittest.TestCase):
    def test_codex_style_conversation(self):
        chat, kinds = responses.to_chat({
            "model": "default_model",
            "instructions": "You are Codex.",
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Sandbox: on."}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "List files."}]},
                {"type": "reasoning", "summary": [], "content": [{"type": "reasoning_text", "text": "Use ls."}]},
                {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": '{"command":["ls"]}'},
                {"type": "function_call_output", "call_id": "c1", "output": "a.txt"},
                {"type": "custom_tool_call", "call_id": "c2", "name": "apply_patch", "input": "*** Begin Patch"},
                {"type": "custom_tool_call_output", "call_id": "c2", "output": "Done"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done."}]},
                {"type": "message", "role": "developer", "content": "Later note."},
                {"type": "function_call", "call_id": "c3", "name": "search", "namespace": "mcp__docs", "arguments": "{}"},
            ],
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_output_tokens": 100,
            "reasoning": {"effort": "medium"},
            "stream": True,
            "store": False,
        })
        self.assertEqual(chat["messages"], [
            {"role": "system", "content": "You are Codex.\n\nSandbox: on."},
            {"role": "user", "content": "List files."},
            {"role": "assistant", "content": "", "reasoning_content": "Use ls.", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": '{"command":["ls"]}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "a.txt"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "apply_patch", "arguments": '{"input": "*** Begin Patch"}'}}]},
            {"role": "tool", "tool_call_id": "c2", "content": "Done"},
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "Later note."},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c3", "type": "function", "function": {"name": "mcp__docs__search", "arguments": "{}"}}]},
        ])
        self.assertEqual(kinds["apply_patch"], {"kind": "custom", "name": "apply_patch", "namespace": None})
        self.assertEqual(kinds["mcp__docs__search"], {"kind": "function", "name": "search", "namespace": "mcp__docs"})
        self.assertEqual([t["function"]["name"] for t in chat["tools"]], ["shell", "apply_patch", "mcp__docs__search"])
        self.assertIn("start: patch", chat["tools"][1]["function"]["description"])
        self.assertEqual(chat["tools"][1]["function"]["parameters"]["required"], ["input"])
        self.assertEqual(chat["tool_choice"], "auto")
        self.assertEqual(chat["max_tokens"], 100)
        self.assertNotIn("chat_template_kwargs", chat)

    def test_string_input_and_no_thinking(self):
        chat, _ = responses.to_chat({"input": "hi", "reasoning": {"effort": "none"}})
        self.assertEqual(chat["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(chat["model"], "default_model")
        self.assertEqual(chat["chat_template_kwargs"], {"enable_thinking": False})

    def test_parallel_calls_share_one_assistant_message(self):
        chat, _ = responses.to_chat({"input": [
            {"type": "message", "role": "user", "content": "go"},
            {"type": "function_call", "call_id": "a", "name": "f", "arguments": "{}"},
            {"type": "function_call", "call_id": "b", "name": "f", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "a", "output": [{"type": "input_text", "text": "1"}]},
            {"type": "function_call_output", "call_id": "b", "output": "2"},
        ]})
        self.assertEqual(len(chat["messages"][1]["tool_calls"]), 2)
        self.assertEqual(chat["messages"][2], {"role": "tool", "tool_call_id": "a", "content": "1"})

    def test_refusals(self):
        cases = [
            ({"input": 3}, "input must be"),
            ({"input": [{"type": "local_shell_call"}]}, "unsupported input item type 'local_shell_call'"),
            ({"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "x"}]}]}, "text-only"),
            ({"input": "x", "tools": [{"type": "teleport"}]}, "unsupported tool type 'teleport'"),
        ]
        for body, message in cases:
            with self.subTest(message):
                with self.assertRaises(responses.BadRequest) as caught:
                    responses.to_chat(body)
                self.assertIn(message, str(caught.exception))


class EventStreamTest(unittest.TestCase):
    def test_reasoning_text_and_calls_become_items(self):
        _, kinds = responses.to_chat({"input": "x", "tools": TOOLS})
        stream = responses.EventStream("default_model", kinds)
        events = stream.start()
        for chunk in (delta(reasoning="Think"), delta(reasoning="ing."), delta(content="Run"), delta(content=" it."),
                      finish("tool_calls", tool_calls=[
                          {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": '{"command":["ls"]}'}},
                          {"id": "c2", "type": "function", "function": {"name": "apply_patch", "arguments": '{"input":"P"}'}},
                          {"id": "c3", "type": "function", "function": {"name": "mcp__docs__search", "arguments": "{}"}}]),
                      USAGE):
            events += stream.feed(chunk)
        events += stream.finish()
        types = [e["type"] for e in events]
        self.assertEqual(types[:2], ["response.created", "response.in_progress"])
        self.assertEqual(types[-1], "response.completed")
        self.assertEqual([e["sequence_number"] for e in events], list(range(1, len(events) + 1)))
        done = [e["item"] for e in events if e["type"] == "response.output_item.done"]
        self.assertEqual([i["type"] for i in done], ["reasoning", "message", "function_call", "custom_tool_call", "function_call"])
        self.assertEqual(done[0]["content"], [{"type": "reasoning_text", "text": "Thinking."}])
        self.assertEqual(done[1]["content"][0]["text"], "Run it.")
        self.assertEqual((done[2]["call_id"], done[2]["arguments"]), ("c1", '{"command":["ls"]}'))
        self.assertEqual((done[3]["call_id"], done[3]["input"]), ("c2", "P"))
        self.assertEqual((done[4]["name"], done[4]["namespace"]), ("search", "mcp__docs"))
        self.assertNotIn("namespace", done[2])
        deltas = "".join(e["delta"] for e in events if e["type"] == "response.output_text.delta")
        self.assertEqual(deltas, "Run it.")
        completed = events[-1]["response"]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["output"], done)
        self.assertEqual(completed["usage"]["input_tokens"], 30)
        self.assertEqual(completed["usage"]["output_tokens"], 7)

    def test_length_limit_is_incomplete(self):
        stream = responses.EventStream("m", {})
        stream.feed(delta(content="abc"))
        stream.feed(finish("length"))
        last = stream.finish()[-1]
        self.assertEqual(last["type"], "response.incomplete")
        self.assertEqual(last["response"]["incomplete_details"], {"reason": "max_output_tokens"})


class ServeTest(unittest.TestCase):
    """responses.serve behind a real HTTP server, with a fake chat endpoint upstream."""

    def setUp(self):
        self.upstream = FakeServer({})
        self.addCleanup(self.upstream.stop)
        chat_url = self.upstream.base_url + "/chat/completions"

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                responses.serve(self, chat_url)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/responses"

    def post(self, body):
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.headers.get("Content-Type"), response.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers.get("Content-Type"), e.read().decode()

    def sent(self):
        return json.loads(self.upstream.requests[-1][2])

    def test_stream(self):
        self.upstream.routes["POST /v1/chat/completions"] = (200, sse(delta(content="ready"), finish("stop"), USAGE))
        status, kind, text = self.post({"model": "default_model", "input": "hi", "stream": True})
        self.assertEqual((status, kind), (200, "text/event-stream"))
        events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
        names = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
        self.assertEqual(names, [e["type"] for e in events])
        # The keepalive became an in_progress event.
        self.assertEqual([e["type"] for e in events[:3]], ["response.created", "response.in_progress", "response.in_progress"])
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual(events[-1]["response"]["output"][0]["content"][0]["text"], "ready")
        self.assertEqual(events[-1]["response"]["usage"]["total_tokens"], 37)
        self.assertEqual(self.sent()["stream_options"], {"include_usage": True})

    def test_whole_response(self):
        self.upstream.routes["POST /v1/chat/completions"] = (200, {
            "choices": [{"message": {"role": "assistant", "content": "ready", "reasoning": "hm"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        })
        status, _, text = self.post({"model": "default_model", "input": "hi", "max_output_tokens": 16})
        self.assertEqual(status, 200)
        body = json.loads(text)
        self.assertEqual(body["object"], "response")
        self.assertEqual([i["type"] for i in body["output"]], ["reasoning", "message"])
        self.assertEqual(self.sent()["max_tokens"], 16)
        self.assertFalse(self.sent()["stream"])

    def test_guard_refusals_pass_through(self):
        # lib/serve.py's guards refuse with HTTP 404 and {"error": message}.
        self.upstream.routes["POST /v1/chat/completions"] = (404, {"error": "4000 in all, over the 2048-token context window."})
        status, _, text = self.post({"input": "hi", "stream": True})
        self.assertEqual(status, 400)
        error = json.loads(text)["error"]
        self.assertEqual(error["code"], "context_length_exceeded")
        self.assertIn("2048-token context window", error["message"])

    def test_bad_requests(self):
        status, _, text = self.post({"input": [{"type": "local_shell_call"}]})
        self.assertEqual(status, 400)
        self.assertIn("local_shell_call", text)
        self.assertEqual(self.upstream.requests, [])

    def test_unreachable_upstream(self):
        self.upstream.stop()
        status, _, text = self.post({"input": "hi"})
        self.assertEqual(status, 502)
        self.assertIn("cannot reach", text)


if __name__ == "__main__":
    unittest.main()
