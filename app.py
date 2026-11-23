from flask import Flask, render_template, request, jsonify
import requests
import json
from ddgs import DDGS

app = Flask(__name__)

# -----------------------------
# FLORANCE AI CONFIGURATION
# -----------------------------

MODEL = "qwen2.5:3b"
OLLAMA_URL = "http://localhost:11434/api/chat"

# Keep the AI model loaded for 30 minutes
KEEP_ALIVE = "30m"


# -----------------------------
# INTERNET SEARCH
# -----------------------------

def search_web(query):
    try:
        with DDGS() as ddgs:
            results = list(
                ddgs.text(query, max_results=3)
            )

        if not results:
            return ""

        information = []

        for item in results:
            title = item.get("title", "")
            body = item.get("body", "")
            url = item.get("href", "")

            information.append(
                f"Title: {title}\n"
                f"Information: {body}\n"
                f"Source: {url}"
            )

        return "\n\n".join(information)

    except Exception as error:
        print("Web search error:", error)
        return ""


# -----------------------------
# DECIDE WHEN TO SEARCH
# -----------------------------

def needs_search(message):

    keywords = [
        "latest",
        "today",
        "current",
        "news",
        "headlines",
        "breaking news",
        "recent",
        "this week",
        "weather",
        "live updates",
        "price today",
        "distance between",
        "distance from",
        "traffic now"
    ]

    message = message.lower()

    return any(word in message for word in keywords)


# -----------------------------
# HOME PAGE
# -----------------------------

@app.route("/")
def home():
    return render_template("index.html")


# -----------------------------
# STATUS CHECK
# -----------------------------

@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "assistant": "Florance AI",
        "model": MODEL,
        "creator": "M. Mehanethra"
    })


# -----------------------------
# CHAT SYSTEM
# -----------------------------

@app.route("/chat", methods=["POST"])
def chat():

    try:

        data = request.get_json(silent=True) or {}

        message = data.get("message", "").strip()

        if not message:
            return jsonify({
                "reply": "Please enter a question."
            })

        # Creator identification
        creator_questions = [
            "who created you",
            "who developed you",
            "who made you",
            "your creator",
            "who is your developer"
        ]

        if any(
            question in message.lower()
            for question in creator_questions
        ):
            return jsonify({
                "reply": (
                    "I am Florance AI, "
                    "developed by M. Mehanethra!"
                )
            })

        # Prepare conversation history
        history = data.get("history", [])

        messages = [
            {
                "role": "system",
                "content": """
You are Florance AI, a helpful general-purpose AI assistant.

Your creator is M. Mehanethra.

Your responsibilities:

1. Answer questions from all subjects.
2. Help with mathematics and calculations.
3. Explain science and technology.
4. Answer history and geography questions.
5. Help users write and understand computer programs.
6. Help with education and general knowledge.
7. Support creative writing and brainstorming.
8. Communicate in simple, understandable language.
9. Give accurate, direct and useful answers.
10. Admit when you are unsure instead of inventing facts.

You are Florance AI. Never claim that you are ChatGPT or Qwen.

For mathematics, show calculations when useful.
For programming, provide working code and explanations.
Keep simple answers concise.
"""
            }
        ]

        # Include recent conversation if supplied
        if isinstance(history, list):

            for item in history[-6:]:

                if not isinstance(item, dict):
                    continue

                role = item.get("role")
                content = item.get("content")

                if (
                    role in ["user", "assistant"]
                    and isinstance(content, str)
                    and content.strip()
                ):
                    messages.append({
                        "role": role,
                        "content": content[:4000]
                    })

        # Add internet information when needed
        if needs_search(message):

            web_information = search_web(message)

            if web_information:

                messages.append({
                    "role": "system",
                    "content": (
                        "Relevant internet search results follow. "
                        "Use them for current information, "
                        "check their relevance, and do not invent "
                        "facts or claim that unverified information "
                        "is confirmed.\n\n"
                        + web_information
                    )
                })

        # Add current question
        messages.append({
            "role": "user",
            "content": message
        })

        # Ask Ollama
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "stream": True,
                "keep_alive": KEEP_ALIVE,
                "options": {
                    "num_ctx": 4096,
                    "num_predict": 700,
                    "temperature": 0.7
                }
            },
            stream=True,
            timeout=(15, 180)
        )

        response.raise_for_status()

        # Collect generated answer
        answer = ""

        for line in response.iter_lines():

            if not line:
                continue

            chunk = json.loads(line)

            answer += chunk.get(
                "message", {}
            ).get("content", "")

            if chunk.get("done"):
                break

        response.close()

        if not answer.strip():
            answer = "I couldn't generate an answer. Please try again."

        return jsonify({
            "reply": answer,
            "assistant": "Florance AI"
        })

    except requests.exceptions.ConnectionError:

        return jsonify({
            "reply": (
                "I cannot connect to Ollama. "
                "Please make sure Ollama is running."
            )
        }), 503

    except requests.exceptions.Timeout:

        return jsonify({
            "reply": (
                "Florance AI is taking too long. "
                "Please try again."
            )
        }), 504

    except Exception as error:

        print("Florance AI error:", error)

        return jsonify({
            "reply": (
                "Something went wrong. "
                "Please try again."
            )
        }), 500


# -----------------------------
# START FLORANCE AI
# -----------------------------

if __name__ == "__main__":

    print("--------------------------------")
    print("       FLORANCE AI")
    print("  Created by M. Mehanethra")
    print("--------------------------------")
    print("Starting Florance AI...")

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
