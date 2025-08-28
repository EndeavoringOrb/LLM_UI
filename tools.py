from googlesearch import search, SearchResult
from bs4 import BeautifulSoup
import requests


# --------------------
# TOOL REGISTRY
# --------------------
def run_calculator(num1, num2, operation):
    if operation == "add":
        return num1 + num2
    elif operation == "subtract":
        return num1 - num2
    elif operation == "multiply":
        return num1 * num2
    elif operation == "divide":
        return num1 / num2 if num2 != 0 else None
    else:
        raise ValueError(f"Unknown operation: {operation}")


def run_web_search(query, num_results=5):
    """Perform a Google search and return a list of results in Markdown format."""
    print(f"Performing Google search for: {query}")
    result_md = ""

    for idx, item in enumerate(
        search(query, num_results=num_results, unique=True, advanced=True)
    ):
        if not isinstance(item, SearchResult):
            continue
        result_md += f"### {idx + 1}. {item.title}\n\n"  # Use H3 for each result
        result_md += f"**Description:** {item.description}\n\n"
        result_md += f"**URL:** [{item.url}]({item.url})\n\n"

    return result_md.strip() if result_md else "No results found."


def run_read_url(url):
    """Fetch the contents of a URL and return as plain text."""
    print(f"Fetching URL: {url}")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"Error fetching URL: {e}"

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text
    except Exception as e:
        return f"Error parsing HTML: {e}"


TOOLS = {
    "calculator": {
        "schema": {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Perform a basic arithmetic operation on two numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "num1": {"type": "number"},
                        "num2": {"type": "number"},
                        "operation": {
                            "type": "string",
                            "enum": ["add", "subtract", "multiply", "divide"],
                        },
                    },
                    "required": ["num1", "num2", "operation"],
                },
            },
        },
        "handler": lambda args: str(
            run_calculator(args["num1"], args["num2"], args["operation"])
        ),
    },
    "web_search": {
        "schema": {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web using Google and return a list of result URLs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "num_results": {
                            "type": "integer",
                            "default": 5,
                            "description": "Number of search results to return (max 10).",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        "handler": lambda args: run_web_search(
            args["query"], args.get("num_results", 5)
        ),
    },
    "read_url": {
        "schema": {
            "type": "function",
            "function": {
                "name": "read_url",
                "description": "Fetch a webpage from a given URL and return its main readable text content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the webpage to read.",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        "handler": lambda args: run_read_url(args["url"]),
    },
}
