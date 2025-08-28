import json
import uuid
import os
import base64
import mimetypes
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    session,
    redirect,
    url_for,
    flash,
)
import pickle
from werkzeug.utils import secure_filename
from tools import TOOLS
from LLM import llama_chat_stream
from functools import wraps
from dotenv import load_dotenv

load_dotenv()
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY")
APP_PASSWORD = os.getenv("APP_PASSWORD")
app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "uploads")
app.config["MAX_CONTENT_LENGTH"] = int(
    os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)
)
LLAMA_URL = os.getenv("LLAMA_URL")

# Ensure upload directory exists
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Supported image formats
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
}

# --------------------
# DATA STRUCTURES
# --------------------


@dataclass
class ChatNode:
    id: str
    role: str
    content: str
    message: dict
    files: List[str] = field(default_factory=list)
    children: List["ChatNode"] = field(default_factory=list)
    parent_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "message": self.message,
            "files": self.files,
            "children": [child.to_dict() for child in self.children],
            "parent_id": self.parent_id,
            "created_at": self.created_at.isoformat(),
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
        }

    @classmethod
    def from_dict(cls, data):
        node = cls(
            id=data["id"],
            role=data["role"],
            content=data["content"],
            message=data["message"],
            files=data.get("files", []),
            parent_id=data.get("parent_id"),
            created_at=datetime.fromisoformat(
                data.get("created_at", datetime.now().isoformat())
            ),
            tool_calls=data.get("tool_calls"),
            tool_results=data.get("tool_results"),
        )
        node.children = [
            cls.from_dict(child_data) for child_data in data.get("children", [])
        ]
        return node


@dataclass
class ChatTree:
    root: ChatNode
    current_node_id: str
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # uuid -> file info

    def to_dict(self):
        return {
            "root": self.root.to_dict(),
            "current_node_id": self.current_node_id,
            "files": self.files,
        }

    @classmethod
    def from_dict(cls, data):
        tree = cls(
            root=ChatNode.from_dict(data["root"]),
            current_node_id=data["current_node_id"],
            files=data.get("files", {}),
        )
        return tree


@dataclass
class Chat:
    id: str
    title: str
    tree: ChatTree
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "tree": self.tree.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data):
        chat = cls(
            id=data["id"],
            title=data["title"],
            tree=ChatTree.from_dict(data["tree"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
        return chat


# Global chats storage
chats: Dict[str, Chat] = {}
global_files: Dict[str, Dict[str, Any]] = {}  # Global files shared across chats

# Tool configuration
enabled_tools = {"calculator": True, "web_search": True, "read_url": True}


# --------------------
# UTILITY FUNCTIONS
# --------------------


def find_node_by_id(node: ChatNode, target_id: str) -> Optional[ChatNode]:
    """Recursively find a node by its ID."""
    if node.id == target_id:
        return node
    for child in node.children:
        result = find_node_by_id(child, target_id)
        if result:
            return result
    return None


def encode_image(image_path: str) -> str:
    """Encode an image file to base64."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def is_image_file(file_info: dict) -> bool:
    """Check if a file is an image based on its MIME type."""
    mime_type = file_info.get("mime_type", "")
    return mime_type in SUPPORTED_IMAGE_TYPES


def format_message_content(message_content: str, files: List[str]) -> List[Dict]:
    """Format message content for multimodal API, handling both text and images."""
    content = []

    # Add images and text files
    for file_uuid in files:
        if file_uuid not in global_files:
            continue

        file_info = global_files[file_uuid]

        if is_image_file(file_info):
            # Handle image files
            try:
                base64_image = encode_image(file_info["path"])
                mime_type = file_info.get("mime_type", "image/jpeg")
                print(mime_type)
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_image}"},
                    }
                )
            except Exception as e:
                print(f"Error encoding image {file_info['filename']}: {e}")
                content.append(
                    {
                        "type": "input_text",
                        "text": f"[Error loading image: {file_info['filename']}]",
                    }
                )
        else:
            # Handle text files (existing behavior)
            file_content = get_file_content(file_uuid)
            if file_content:
                content.append(
                    {
                        "type": "input_text",
                        "text": f"File: {file_info.get('filename', 'unknown')}\n{file_content}",
                    }
                )

    # Add text content if present
    if message_content.strip():
        content.append({"type": "text", "text": message_content})

    return content


def get_conversation_path(chat_id: str, node_id: str) -> List[Dict]:
    """Get the conversation path from root to the specified node."""
    if chat_id not in chats:
        return []

    chat = chats[chat_id]
    path = []
    current = find_node_by_id(chat.tree.root, node_id)

    if not current:
        return path

    # Build path from current node back to root
    temp_path = []
    while current:
        # Format message for multimodal API
        message = current.message.copy()

        if current.role == "user" and ("files" in message and message["files"]):
            # Convert user messages with files to multimodal format
            content = format_message_content(
                message.get("content", ""), message["files"]
            )
            message["content"] = content
            # Remove the files field as it's now incorporated into content
            if "files" in message:
                del message["files"]

        temp_path.append(message)

        if current.parent_id:
            current = find_node_by_id(chat.tree.root, current.parent_id)
        else:
            break

    # Reverse to get root-to-current order
    path = list(reversed(temp_path))
    return path


def generate_chat_title(content: str) -> str:
    """Generate a title from the first message content."""
    # Take first 30 characters and clean up
    title = content.strip()[:30]
    if len(content) > 30:
        title += "..."
    return title


def save_chats():
    """Save all chats to disk."""
    data = {
        "chats": {chat_id: chat.to_dict() for chat_id, chat in chats.items()},
        "global_files": global_files,
        "enabled_tools": enabled_tools,
    }
    with open("chats.pkl", "wb") as f:
        pickle.dump(data, f)


def load_chats():
    """Load all chats from disk."""
    global chats, global_files, enabled_tools
    try:
        with open("chats.pkl", "rb") as f:
            data = pickle.load(f)
            chats = {
                chat_id: Chat.from_dict(chat_data)
                for chat_id, chat_data in data.get("chats", {}).items()
            }
            global_files = data.get("global_files", {})
            enabled_tools = data.get(
                "enabled_tools",
                {"calculator": True, "web_search": True, "read_url": True},
            )
    except FileNotFoundError:
        pass  # Use default empty chats


def get_file_content(file_uuid: str) -> str:
    """Get the content of a text file by its UUID."""
    if file_uuid not in global_files:
        return ""

    file_info = global_files[file_uuid]

    # Don't try to read image files as text
    if is_image_file(file_info):
        return f"[Image: {file_info.get('filename', 'unknown')}]"

    try:
        with open(file_info["path"], "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


def create_new_chat() -> str:
    """Create a new chat and return its ID."""
    chat_id = str(uuid.uuid4())

    # Create new chat tree
    tree = ChatTree(
        root=ChatNode(
            id=str(uuid.uuid4()),
            role="system",
            content="You are a helpful AI assistant.",
            message={"role": "system", "content": "You are a helpful AI assistant."},
        ),
        current_node_id="",
        files={},
    )
    tree.current_node_id = tree.root.id

    # Create new chat
    chat = Chat(id=chat_id, title="New Chat", tree=tree)

    chats[chat_id] = chat
    save_chats()
    return chat_id


# --------------------
# FLASK ROUTES
# --------------------


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        else:
            flash("Incorrect password", "error")
    return render_template("login.html")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("chat.html")


# Chat management routes
@app.route("/api/chats")
@login_required
def list_chats():
    """Get list of all chats."""
    chat_list = []
    for chat in chats.values():
        chat_list.append(
            {
                "id": chat.id,
                "title": chat.title,
                "created_at": chat.created_at.isoformat(),
                "updated_at": chat.updated_at.isoformat(),
            }
        )

    # Sort by updated_at descending
    chat_list.sort(key=lambda x: x["updated_at"], reverse=True)
    return jsonify(chat_list)


@app.route("/api/chats/new", methods=["POST"])
@login_required
def create_chat():
    """Create a new chat."""
    chat_id = create_new_chat()
    return jsonify({"success": True, "chat_id": chat_id})


@app.route("/api/chats/<chat_id>", methods=["DELETE"])
@login_required
def delete_chat(chat_id):
    """Delete a chat."""
    if chat_id in chats:
        del chats[chat_id]
        save_chats()
        return jsonify({"success": True})
    return jsonify({"error": "Chat not found"}), 404


@app.route("/api/chats/<chat_id>/tree")
@login_required
def get_chat_tree(chat_id):
    """Get the chat tree for a specific chat."""
    if chat_id not in chats:
        return jsonify({"error": "Chat not found"}), 404

    chat = chats[chat_id]
    return jsonify(
        {
            "tree": chat.tree.to_dict(),
            "current_node_id": chat.tree.current_node_id,
            "title": chat.title,
        }
    )


@app.route("/api/chats/<chat_id>/send", methods=["POST"])
@login_required
def send_message(chat_id):
    """Send a message to a specific chat."""
    if chat_id not in chats:
        return jsonify({"error": "Chat not found"}), 404

    data = request.json
    if not data:
        return jsonify({"error": "Bad data"}), 400

    message_content = data.get("message", "")
    files = data.get("files", [])

    # Allow empty message if there are files (especially images)
    if not message_content.strip() and not files:
        return jsonify({"error": "Message cannot be empty"}), 400

    chat = chats[chat_id]

    # Create new user message node
    user_node = ChatNode(
        id=str(uuid.uuid4()),
        role="user",
        content=message_content,
        message={"role": "user", "content": message_content, "files": files},
        files=files,
        parent_id=chat.tree.current_node_id,
    )

    # Add user node to current node's children
    current_node = find_node_by_id(chat.tree.root, chat.tree.current_node_id)

    if not current_node:
        return jsonify({"error": "Node not found"}), 404

    current_node.children.append(user_node)

    # Update current node to the user message
    chat.tree.current_node_id = user_node.id

    # Update chat title if this is the first user message
    updated_title = None
    if chat.title == "New Chat":
        if message_content.strip():
            chat.title = generate_chat_title(message_content)
        elif files:
            # Generate title based on file types if no text content
            image_count = sum(
                1 for f in files if f in global_files and is_image_file(global_files[f])
            )
            file_count = len(files)
            if image_count > 0:
                chat.title = f"Images and files ({file_count} files)"
            else:
                chat.title = f"Files ({file_count} files)"
        updated_title = chat.title

    # Update chat timestamp
    chat.updated_at = datetime.now()

    save_chats()

    response_data = {"success": True, "node_id": user_node.id}
    if updated_title:
        response_data["updated_title"] = updated_title

    return jsonify(response_data)


@app.route("/api/chats/<chat_id>/stream/<node_id>")
@login_required
def stream_response(chat_id, node_id):
    """Stream AI response for a specific chat and node."""
    if chat_id not in chats:
        return Response("Chat not found", status=404)

    def generate():
        try:
            # Get conversation path up to this node (already formatted for multimodal)
            messages = get_conversation_path(chat_id, node_id)

            yield "data: " + json.dumps(
                {"type": "status", "content": "Starting response..."}
            ) + "\n\n"

            # Stream initial response
            response_generator = llama_chat_stream(messages, enabled_tools)
            assistant_message = None

            for chunk_data in response_generator:
                chunk = json.loads(chunk_data)

                if chunk["type"] == "complete":
                    assistant_message = chunk["message"]
                    break
                else:
                    yield f"data: {chunk_data}\n\n"

            if not assistant_message:
                yield "data: " + json.dumps(
                    {"type": "error", "content": "No response from model"}
                ) + "\n\n"
                return

            current_node = find_node_by_id(chats[chat_id].tree.root, node_id)

            if not current_node:
                yield "data: " + json.dumps(
                    {"type": "error", "content": "Node not found"}
                ) + "\n\n"
                return

            # Handle tool calls if present
            if "tool_calls" in assistant_message and assistant_message["tool_calls"]:
                # Add assistant message with tool calls to conversation
                assistant_node = ChatNode(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    content=assistant_message.get("content", ""),
                    message=assistant_message,
                    tool_calls=assistant_message["tool_calls"],
                    parent_id=node_id,
                )
                current_node.children.append(assistant_node)
                chats[chat_id].tree.current_node_id = assistant_node.id
                current_node = assistant_node

                # Execute tool calls
                tool_results = []
                for tool_call in assistant_message["tool_calls"]:
                    tool_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except json.JSONDecodeError:
                        continue

                    if tool_name in TOOLS and enabled_tools.get(tool_name, False):
                        yield "data: " + json.dumps(
                            {
                                "type": "tool_call",
                                "name": tool_name,
                                "args": args,
                            }
                        ) + "\n\n"
                        result = TOOLS[tool_name]["handler"](args)
                        tool_results.append(
                            {"tool_call_id": tool_call.get("id"), "content": result}
                        )

                        # Send tool result to UI
                        yield "data: " + json.dumps(
                            {
                                "type": "tool_result",
                                "tool_call_id": tool_call.get("id"),
                                "result": result,
                            }
                        ) + "\n\n"

                assistant_node.tool_results = tool_results
                save_chats()

                # Generate final response with tool results
                messages = get_conversation_path(chat_id, assistant_node.id)
                for result in tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "content": result["content"],
                            "tool_call_id": result["tool_call_id"],
                        }
                    )

                # Stream final response after tool calls
                yield "data: " + json.dumps(
                    {"type": "status", "content": "Processing tool results..."}
                ) + "\n\n"

                final_generator = llama_chat_stream(messages, enabled_tools)
                for chunk_data in final_generator:
                    chunk = json.loads(chunk_data)

                    if chunk["type"] == "complete":
                        assistant_message = chunk["message"]
                        break
                    else:
                        yield f"data: {chunk_data}\n\n"

            if current_node.role == "assistant":
                current_node.content = assistant_message.get("content", "")
                new_id = current_node.id
            else:
                # Create assistant response node
                assistant_node = ChatNode(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    content=assistant_message.get("content", ""),
                    message=assistant_message,
                    parent_id=node_id,
                )

                current_node.children.append(assistant_node)

                # Update current node to the assistant response
                chats[chat_id].tree.current_node_id = assistant_node.id
                new_id = assistant_node.id

            # Update chat timestamp
            chats[chat_id].updated_at = datetime.now()

            save_chats()

            yield "data: " + json.dumps(
                {"type": "finished", "node_id": new_id}
            ) + "\n\n"

        except Exception as e:
            yield "data: " + json.dumps(
                {"type": "error", "content": f"Error: {str(e)}"}
            ) + "\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/chats/<chat_id>/edit", methods=["POST"])
@login_required
def edit_message(chat_id):
    """Edit a message in a specific chat."""
    if chat_id not in chats:
        return jsonify({"error": "Chat not found"}), 404

    data = request.json
    if not data:
        return jsonify({"error": "Bad data"}), 400

    node_id = data.get("node_id")
    new_content = data.get("content", "")
    new_files = data.get("files", [])

    chat = chats[chat_id]
    node = find_node_by_id(chat.tree.root, node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404

    # Create new sibling node with edited content
    parent_id = node.parent_id
    if not parent_id:
        return jsonify({"error": "Cannot edit root node"}), 400

    parent_node = find_node_by_id(chat.tree.root, parent_id)
    if not parent_node:
        return jsonify({"error": "Parent node not found"}), 404

    new_message = node.message
    new_message["content"] = new_content
    new_message["files"] = new_files
    new_node = ChatNode(
        id=str(uuid.uuid4()),
        role=node.role,
        content=new_content,
        message=new_message,
        files=new_files,
        parent_id=parent_id,
    )

    parent_node.children.append(new_node)
    chat.tree.current_node_id = new_node.id

    # Update chat timestamp
    chat.updated_at = datetime.now()

    save_chats()

    # Return whether this was a user message (for auto-generation)
    return jsonify(
        {
            "success": True,
            "node_id": new_node.id,
            "should_generate": node.role == "user",
        }
    )


@app.route("/api/chats/<chat_id>/continue/<node_id>", methods=["POST"])
@login_required
def continue_message(chat_id, node_id):
    """Continue generating from an existing assistant message."""
    if chat_id not in chats:
        return jsonify({"error": "Chat not found"}), 404

    chat = chats[chat_id]
    node = find_node_by_id(chat.tree.root, node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404

    if node.role != "assistant":
        return jsonify({"error": "Can only continue assistant messages"}), 400

    # Update current node to this assistant message
    chat.tree.current_node_id = node_id

    # Update chat timestamp
    chat.updated_at = datetime.now()

    save_chats()

    return jsonify({"success": True, "node_id": node_id})


# File management routes
@app.route("/api/files/upload", methods=["POST"])
@login_required
def upload_file():
    """Upload a file."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    if file:
        filename = secure_filename(file.filename)
        file_uuid = str(uuid.uuid4())
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{file_uuid}_{filename}")

        file.save(file_path)

        # Detect MIME type
        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            # Fallback MIME type detection
            if filename.lower().endswith((".jpg", ".jpeg")):
                mime_type = "image/jpeg"
            elif filename.lower().endswith(".png"):
                mime_type = "image/png"
            elif filename.lower().endswith(".gif"):
                mime_type = "image/gif"
            elif filename.lower().endswith(".webp"):
                mime_type = "image/webp"
            elif filename.lower().endswith(".bmp"):
                mime_type = "image/bmp"
            else:
                mime_type = "application/octet-stream"

        global_files[file_uuid] = {
            "filename": filename,
            "path": file_path,
            "size": os.path.getsize(file_path),
            "mime_type": mime_type,
            "uploaded_at": datetime.now().isoformat(),
            "is_image": is_image_file({"mime_type": mime_type}),
        }

        save_chats()

        return jsonify(
            {
                "success": True,
                "file_uuid": file_uuid,
                "filename": filename,
                "mime_type": mime_type,
                "is_image": global_files[file_uuid]["is_image"],
            }
        )


@app.route("/api/files")
@login_required
def list_files():
    """Get list of all files."""
    return jsonify(global_files)


@app.route("/api/files/<file_uuid>")
@login_required
def get_file(file_uuid):
    """Serve a file by its UUID."""
    if file_uuid not in global_files:
        return jsonify({"error": "File not found"}), 404

    file_info = global_files[file_uuid]
    return app.send_static_file(file_info["path"])


# Tool management routes
@app.route("/api/tools")
@login_required
def get_tools():
    """Get available tools and their enabled status."""
    return jsonify({"tools": list(TOOLS.keys()), "enabled": enabled_tools})


@app.route("/api/tools/toggle", methods=["POST"])
@login_required
def toggle_tool():
    """Toggle a tool on/off."""
    data = request.json

    if not data:
        return jsonify({"error": "Bad data"}), 400

    tool_name = data.get("tool_name")
    enabled = data.get("enabled", False)

    if tool_name in TOOLS:
        enabled_tools[tool_name] = enabled
        save_chats()
        return jsonify({"success": True})

    return jsonify({"error": "Tool not found"}), 404


if __name__ == "__main__":
    load_chats()
    app.run(debug=True, host="0.0.0.0", port=55551)
