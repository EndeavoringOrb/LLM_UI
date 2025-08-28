import json
import requests
from tools import TOOLS
from dotenv import load_dotenv

load_dotenv()
import os

LLAMA_URL = os.getenv("LLAMA_URL")


# --------------------
# MODEL CALL
# --------------------
def llama_chat_stream(messages, enabled_tools):
    """Send chat messages to the llama-server and stream the response."""
    # Filter tools based on enabled_tools
    available_tools = [
        tool["schema"]
        for tool_name, tool in TOOLS.items()
        if enabled_tools.get(tool_name, False)
    ]
    print(messages)

    payload = {
        "messages": messages,
        "tools": available_tools,
        "stream": True,
        "timings_per_token": True,
    }

    resp = requests.post(
        LLAMA_URL,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        stream=True,
    )
    resp.raise_for_status()

    content = ""
    reasoning_content = ""
    tool_calls = {}
    final_data = None

    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        if line.strip() == "data: [DONE]":
            break

        try:
            chunk = json.loads(line[6:])
            if len(chunk["choices"]) == 0:
                break

            final_data = chunk

            if "timings" in chunk:
                yield json.dumps({"type": "timings", "timings": chunk["timings"]})

            delta = chunk["choices"][0].get("delta", {})

            if "content" in delta and delta["content"]:
                yield json.dumps({"type": "content", "content": delta["content"]})
                content += delta["content"]

            if "reasoning_content" in delta and delta.get("reasoning_content"):
                yield json.dumps(
                    {
                        "type": "reasoning_content",
                        "content": delta["reasoning_content"],
                    }
                )
                reasoning_content += delta["reasoning_content"]

            if "tool_calls" in delta:
                for tc in delta["tool_calls"]:
                    idx = tc["index"]
                    existing = tool_calls.get(
                        idx,
                        {
                            "id": tc.get("id"),
                            "type": tc.get("type"),
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if "function" in tc and "name" in tc["function"]:
                        existing["function"]["name"] += tc["function"]["name"]
                    if "function" in tc and "arguments" in tc["function"]:
                        existing["function"]["arguments"] += tc["function"]["arguments"]
                    if "id" in tc:
                        existing["id"] = tc["id"]
                    if "type" in tc:
                        existing["type"] = tc["type"]
                    tool_calls[idx] = existing

        except (json.JSONDecodeError, KeyError):
            continue

    if final_data:
        message = {
            "role": "assistant",
            "content": content,
            "reasoning_content": reasoning_content,
        }
        if tool_calls:
            message["tool_calls"] = list(tool_calls.values())
        yield json.dumps({"type": "complete", "message": message})
