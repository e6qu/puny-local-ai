"""Serves the OpenAI Responses API on top of mlx_lm.server's Chat Completions.

Codex speaks only the Responses API (POST /v1/responses), which mlx-lm 0.32.2
does not have. lib/serve.py routes that path here, in the same server, so
Codex uses the same address as OpenCode and Pi. Each Responses request is
translated into a Chat Completions request, sent to the server's own
/v1/chat/completions, and the reply is translated back, streamed or not.
The chat request goes through the same memory guards as any other.

What is translated:
  - instructions, and developer or system messages before the first user
    message, become the one system message Qwen's chat template allows at
    the start; later developer messages become user messages.
  - user and assistant messages, function calls and their outputs, and
    custom (freeform) tool calls, such as Codex's apply_patch, which become
    function calls with one string argument, "input".
  - reasoning items become the reasoning_content of the next assistant
    message, which Qwen's chat template renders as thinking.
  - function and custom tools, and the tools in a namespace, such as an MCP
    server's, which the model sees as NAMESPACE__NAME. Tools that OpenAI runs
    on its own servers, such as web_search, cannot run here and are left out.
  - max_output_tokens, temperature, top_p, tool_choice, and reasoning effort
    "none" or "minimal", which turns thinking off.

The reply arrives as reasoning, text and tool-call deltas and is sent as
Responses events: response.created, output items with their deltas, and
response.completed with the token usage, or response.incomplete when the
reply hit max_output_tokens. Chunks the server sends while it processes a
long prompt become response.in_progress events, so the client's idle timer
does not run out.

Runs in the mlx-lm environment, but needs only the standard library.
"""

import json
import logging
import time
import urllib.error
import urllib.request
import uuid

PATHS = ("/v1/responses", "/responses")
# Tools that OpenAI runs on its own servers; a local server cannot provide them.
# Codex's tool_search needs a model trained for it, and Codex offers it only
# to such models.
HOSTED_TOOLS = ("web_search", "web_search_preview", "file_search", "image_generation", "computer_use_preview",
                "code_interpreter", "mcp", "tool_search")
UPSTREAM_TIMEOUT_SECONDS = 3600


class BadRequest(Exception):
    pass


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


# ------------------------------------------------------------ requests


def content_text(content):
    """The text of a message's content, a string or a list of parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        kind = part.get("type")
        if kind in ("input_text", "output_text", "text", "summary_text", "reasoning_text", "refusal"):
            parts.append(part.get("text") or part.get("refusal") or "")
        elif kind in ("input_image", "input_file"):
            raise BadRequest(f"{kind} parts are not supported: this server runs a text-only model")
        else:
            raise BadRequest(f"unsupported content part type {kind!r}")
    return "".join(parts)


def reasoning_text(item):
    text = content_text(item.get("content") or [])
    return text or content_text(item.get("summary") or [])


def chat_name(name, namespace=None):
    """The name the model sees. Tools in a namespace, such as an MCP server's, get it as a prefix."""
    return f"{namespace}__{name}" if namespace else name


def chat_tools(tools):
    """Chat Completions tools, and what each chat name stands for: its kind, name and namespace."""
    converted, kinds, skipped = [], {}, []

    def add(tool, namespace=None):
        kind = tool.get("type")
        if kind not in ("function", "custom"):
            raise BadRequest(f"unsupported tool type {kind!r}")
        name = chat_name(tool["name"], namespace)
        description = tool.get("description") or ""
        if kind == "function":
            parameters = tool.get("parameters") or {"type": "object", "properties": {}}
        elif kind == "custom":
            # A freeform tool takes raw text, such as a patch. The model sees
            # it as a function with one string argument.
            grammar = tool.get("format") or {}
            if grammar.get("type") == "grammar" and grammar.get("definition"):
                description += (
                    f"\n\nThe input argument must follow this {grammar.get('syntax', '')} grammar:\n"
                    + grammar["definition"]
                )
            parameters = {
                "type": "object",
                "properties": {"input": {"type": "string", "description": "The raw input for the tool."}},
                "required": ["input"],
            }
        function = {"name": name, "parameters": parameters}
        if description.strip():
            function["description"] = description.strip()
        converted.append({"type": "function", "function": function})
        kinds[name] = {"kind": kind, "name": tool["name"], "namespace": namespace}

    for tool in tools or []:
        kind = tool.get("type")
        if kind == "namespace":
            for member in tool.get("tools") or []:
                add(member, tool["name"])
        elif kind in HOSTED_TOOLS:
            skipped.append(kind)
        else:
            add(tool)
    if skipped:
        logging.info(f"Responses API: left out tools that only OpenAI's servers run: {', '.join(skipped)}")
    return converted, kinds


def chat_tool_choice(choice):
    if choice is None or isinstance(choice, str):
        return choice
    if choice.get("type") in ("function", "custom") and choice.get("name"):
        return {"type": "function", "function": {"name": chat_name(choice["name"], choice.get("namespace"))}}
    raise BadRequest(f"unsupported tool_choice {json.dumps(choice)}")


def to_chat(body):
    """Translates a Responses request body into (chat request body, tool kinds by name)."""
    if not isinstance(body, dict):
        raise BadRequest("the request body must be a JSON object")
    items = body.get("input")
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    if not isinstance(items, list):
        raise BadRequest("input must be a string or a list of items")

    system = [body["instructions"]] if body.get("instructions") else []
    messages = []
    reasoning = None

    def assistant():
        """The assistant message to add to: the last one, unless a user or tool message came after it."""
        nonlocal reasoning
        if not (messages and messages[-1]["role"] == "assistant"):
            messages.append({"role": "assistant", "content": ""})
        if reasoning:
            messages[-1]["reasoning_content"] = messages[-1].get("reasoning_content", "") + reasoning
            reasoning = None
        return messages[-1]

    def add_call(call_id, name, arguments):
        message = assistant()
        message.setdefault("tool_calls", []).append(
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        )

    for item in items:
        if not isinstance(item, dict):
            raise BadRequest("each input item must be a JSON object")
        kind = item.get("type", "message")
        if kind == "message":
            role, text = item.get("role"), content_text(item.get("content"))
            if role in ("system", "developer"):
                if messages:
                    messages.append({"role": "user", "content": text})
                else:
                    system.append(text)
            elif role == "user":
                messages.append({"role": "user", "content": text})
            elif role == "assistant":
                message = assistant()
                if message.get("tool_calls"):
                    messages.append({"role": "assistant", "content": text})
                else:
                    message["content"] += text
            else:
                raise BadRequest(f"unsupported message role {role!r}")
        elif kind == "function_call":
            add_call(item["call_id"], chat_name(item["name"], item.get("namespace")), item.get("arguments") or "{}")
        elif kind == "custom_tool_call":
            add_call(item["call_id"], chat_name(item["name"], item.get("namespace")),
                     json.dumps({"input": item.get("input", "")}))
        elif kind in ("function_call_output", "custom_tool_call_output"):
            output = item.get("output")
            messages.append({
                "role": "tool",
                "tool_call_id": item["call_id"],
                "content": output if isinstance(output, str) else content_text(output),
            })
        elif kind == "reasoning":
            reasoning = (reasoning or "") + reasoning_text(item)
        else:
            raise BadRequest(f"unsupported input item type {kind!r}")
    if system:
        messages.insert(0, {"role": "system", "content": "\n\n".join(system)})

    tools, kinds = chat_tools(body.get("tools"))
    chat = {"model": body.get("model", "default_model"), "messages": messages}
    if tools:
        chat["tools"] = tools
        if body.get("tool_choice") is not None:
            chat["tool_choice"] = chat_tool_choice(body["tool_choice"])
    for source, target in (("max_output_tokens", "max_tokens"), ("temperature", "temperature"), ("top_p", "top_p")):
        if body.get(source) is not None:
            chat[target] = body[source]
    effort = (body.get("reasoning") or {}).get("effort")
    if effort in ("none", "minimal"):
        chat["chat_template_kwargs"] = {"enable_thinking": False}
    return chat, kinds


# ------------------------------------------------------------ replies


def response_object(response_id, model, created, status, output, usage=None, incomplete=None):
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": status,
        "model": model,
        "output": output,
        "usage": usage,
        "error": None,
        "incomplete_details": {"reason": incomplete} if incomplete else None,
    }


def usage_object(usage):
    if not usage:
        return None
    return {
        "input_tokens": usage.get("prompt_tokens", 0),
        "input_tokens_details": {"cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)},
        "output_tokens": usage.get("completion_tokens", 0),
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": usage.get("total_tokens", 0),
    }


def call_item(call, kinds):
    """A function_call item, or a custom_tool_call item for a freeform tool."""
    function = call.get("function") or {}
    name, arguments = function.get("name", ""), function.get("arguments") or "{}"
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    call_id = call.get("id") or new_id("call")
    tool = kinds.get(name) or {"kind": "function", "name": name, "namespace": None}
    named = {"name": tool["name"], **({"namespace": tool["namespace"]} if tool["namespace"] else {})}
    if tool["kind"] == "custom":
        try:
            text = json.loads(arguments).get("input", "")
        except (ValueError, AttributeError):
            text = arguments
        return {"type": "custom_tool_call", "id": new_id("ctc"), "call_id": call_id, **named,
                "input": text if isinstance(text, str) else json.dumps(text), "status": "completed"}
    return {"type": "function_call", "id": new_id("fc"), "call_id": call_id, **named,
            "arguments": arguments, "status": "completed"}


def reasoning_item(item_id, text):
    return {"type": "reasoning", "id": item_id, "summary": [],
            "content": [{"type": "reasoning_text", "text": text}]}


def message_item(item_id, text):
    return {"type": "message", "id": item_id, "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def from_chat(chat, model, kinds):
    """A whole Responses object from a whole chat completion."""
    choice = (chat.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output = []
    if message.get("reasoning"):
        output.append(reasoning_item(new_id("rs"), message["reasoning"]))
    if message.get("content"):
        output.append(message_item(new_id("msg"), message["content"]))
    output += [call_item(c, kinds) for c in message.get("tool_calls") or []]
    incomplete = "max_output_tokens" if choice.get("finish_reason") == "length" else None
    return response_object(new_id("resp"), model, int(time.time()), "incomplete" if incomplete else "completed",
                           output, usage_object(chat.get("usage")), incomplete)


class EventStream:
    """Turns chat completion chunks into Responses events.

    feed() takes one parsed chunk and returns the events it produces;
    finish() returns the closing events. Reasoning and text each become one
    output item while they last; a tool call becomes an item of its own.
    """

    def __init__(self, model, kinds):
        self.kinds = kinds  # what each tool name the model sees stands for, from chat_tools
        self.output = []
        self.sequence = 0
        self.open = None  # (kind, item id, text so far) of the item being streamed
        self.usage = None
        self.finish_reason = None
        self.response = response_object(new_id("resp"), model, int(time.time()), "in_progress", [])

    def event(self, kind, **fields):
        self.sequence += 1
        return {"type": kind, "sequence_number": self.sequence, **fields}

    def start(self):
        return [self.event("response.created", response=self.response),
                self.event("response.in_progress", response=self.response)]

    def keepalive(self):
        return [self.event("response.in_progress", response=self.response)]

    def close_open(self):
        if self.open is None:
            return []
        kind, item_id, text = self.open
        self.open = None
        index = len(self.output)
        if kind == "reasoning":
            item = reasoning_item(item_id, text)
            events = [
                self.event("response.reasoning_text.done", item_id=item_id, output_index=index, content_index=0, text=text),
            ]
        else:
            item = message_item(item_id, text)
            part = item["content"][0]
            events = [
                self.event("response.output_text.done", item_id=item_id, output_index=index, content_index=0, text=text),
                self.event("response.content_part.done", item_id=item_id, output_index=index, content_index=0, part=part),
            ]
        self.output.append(item)
        return events + [self.event("response.output_item.done", output_index=index, item=item)]

    def delta(self, kind, text):
        events = []
        if self.open is not None and self.open[0] != kind:
            events += self.close_open()
        index = len(self.output)
        if self.open is None:
            item_id = new_id("rs" if kind == "reasoning" else "msg")
            self.open = (kind, item_id, "")
            if kind == "reasoning":
                events.append(self.event("response.output_item.added", output_index=index,
                                         item={"type": "reasoning", "id": item_id, "summary": [], "content": []}))
            else:
                events.append(self.event("response.output_item.added", output_index=index, item={
                    "type": "message", "id": item_id, "role": "assistant", "status": "in_progress", "content": []}))
                events.append(self.event("response.content_part.added", item_id=item_id, output_index=index,
                                         content_index=0, part={"type": "output_text", "text": "", "annotations": []}))
        _, item_id, so_far = self.open
        self.open = (kind, item_id, so_far + text)
        name = "response.reasoning_text.delta" if kind == "reasoning" else "response.output_text.delta"
        return events + [self.event(name, item_id=item_id, output_index=index, content_index=0, delta=text)]

    def tool_call(self, call):
        events = self.close_open()
        item = call_item(call, self.kinds)
        index = len(self.output)
        added = dict(item, status="in_progress")
        added["arguments" if item["type"] == "function_call" else "input"] = ""
        events.append(self.event("response.output_item.added", output_index=index, item=added))
        if item["type"] == "function_call":
            events += [
                self.event("response.function_call_arguments.delta", item_id=item["id"], output_index=index, delta=item["arguments"]),
                self.event("response.function_call_arguments.done", item_id=item["id"], output_index=index, arguments=item["arguments"]),
            ]
        else:
            events += [
                self.event("response.custom_tool_call_input.delta", item_id=item["id"], output_index=index, delta=item["input"]),
                self.event("response.custom_tool_call_input.done", item_id=item["id"], output_index=index, input=item["input"]),
            ]
        self.output.append(item)
        return events + [self.event("response.output_item.done", output_index=index, item=item)]

    def feed(self, chunk):
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        events = []
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("reasoning"):
                events += self.delta("reasoning", delta["reasoning"])
            if delta.get("content"):
                events += self.delta("text", delta["content"])
            for call in delta.get("tool_calls") or []:
                events += self.tool_call(call)
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
        return events

    def finish(self):
        events = self.close_open()
        incomplete = "max_output_tokens" if self.finish_reason == "length" else None
        self.response = response_object(
            self.response["id"], self.response["model"], self.response["created_at"],
            "incomplete" if incomplete else "completed", self.output, usage_object(self.usage), incomplete,
        )
        return events + [self.event("response.incomplete" if incomplete else "response.completed", response=self.response)]

    def failed(self, message):
        events = self.close_open()
        self.response = dict(self.response, status="failed", output=self.output,
                             error={"code": "server_error", "message": message})
        return events + [self.event("response.failed", response=self.response)]


# ------------------------------------------------------------ HTTP


def error_body(message, status):
    error = {"message": message, "type": "invalid_request_error" if status < 500 else "server_error"}
    if "context window" in message:
        # The code OpenAI uses, which clients such as Codex recognize.
        error["code"] = "context_length_exceeded"
    return {"error": error}


def send_json(handler, status, payload):
    data = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    handler.wfile.flush()


def upstream_error(error):
    """The message of an HTTP error from the chat endpoint."""
    raw = error.read().decode(errors="replace")
    try:
        message = json.loads(raw)["error"]
        if isinstance(message, dict):
            message = message.get("message", json.dumps(message))
    except (ValueError, KeyError, TypeError):
        message = raw or f"HTTP {error.code}"
    return message


def serve(handler, chat_url):
    """Answers one POST to /v1/responses on handler, a BaseHTTPRequestHandler."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
        body = json.loads(handler.rfile.read(length) or b"null")
        chat, kinds = to_chat(body)
    except (ValueError, KeyError, TypeError, BadRequest) as e:
        message = str(e) if isinstance(e, BadRequest) else f"cannot read the request: {e!r}"
        send_json(handler, 400, error_body(message, 400))
        return
    stream = bool(body.get("stream"))
    model = chat["model"]
    chat["stream"] = stream
    if stream:
        chat["stream_options"] = {"include_usage": True}
    request = urllib.request.Request(chat_url, data=json.dumps(chat).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        upstream = urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as e:
        # The memory guards refuse requests this way, with status 404.
        with e:
            send_json(handler, 400, error_body(upstream_error(e), 400))
        return
    except OSError as e:
        send_json(handler, 502, error_body(f"cannot reach {chat_url}: {e}", 502))
        return
    with upstream:
        if not stream:
            send_json(handler, 200, from_chat(json.load(upstream), model, kinds))
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
        events = EventStream(model, kinds)

        def send(batch):
            for event in batch:
                handler.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
            handler.wfile.flush()

        send(events.start())
        try:
            for raw in upstream:
                line = raw.decode().strip()
                if line.startswith(":"):
                    send(events.keepalive())  # mlx-lm's progress while it reads a long prompt
                elif line.startswith("data:"):
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    send(events.feed(json.loads(data)))
            send(events.finish())
        except (BrokenPipeError, ConnectionResetError):
            raise  # the client went away; closing the upstream stops generation
        except (OSError, ValueError) as e:
            send(events.failed(f"the chat stream broke off: {e!r}"))
