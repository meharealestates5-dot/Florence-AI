
import os
import re
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "assistant": "Uniteaa AI"
    })
# -----------------------------
# UNITEAA AI CONFIGURATION
# -----------------------------

ASSISTANT_NAME = "Uniteaa AI"
CREATOR_NAME = "M. Mehanethra"

FATHER_NAME = "K. Manoharan"
MOTHER_NAME = "K. Marakatham"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL", "gemini-2.5-flash"
).strip()

GROQ_MODEL = os.environ.get(
    "GROQ_MODEL", "openai/gpt-oss-120b"
).strip()

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = f"""
You are {ASSISTANT_NAME}, a helpful, friendly and intelligent AI assistant.

Your creator is {CREATOR_NAME}.

If someone asks who created you, answer:
"I was created by {CREATOR_NAME}."

If someone asks for your creator's parents' names, answer:
"My creator's father's name is {FATHER_NAME}, and her mother's name is {MOTHER_NAME}."

Do not reveal any other personal information about the creator.

Answer questions clearly, accurately and in simple language.
If you are unsure about an answer, say so instead of inventing information.
Do not claim to have performed live web searches.
"""

# -----------------------------
# HOME PAGE
# -----------------------------

@app.route("/")
def home():
    return render_template("index.html")


# -----------------------------
# CREATOR INFORMATION
# -----------------------------

def creator_answer(message):
    text = message.lower()

    creator_questions = [
        "who created you",
        "who is your creator",
        "who made you",
        "who developed you",
        "who is the founder",
        "who is your developer"
    ]

    parent_questions = [
        "parents name",
        "parent names",
        "father and mother",
        "who are your creator parents",
        "creator's parents",
        "creators parents"
    ]

    if any(q in text for q in parent_questions):
        return (
            f"My creator's father's name is {FATHER_NAME}, "
            f"and her mother's name is {MOTHER_NAME}."
        )

    if any(q in text for q in creator_questions):
        return f"I was created by {CREATOR_NAME}."

    return None


# -----------------------------
# DISTANCE CALCULATOR
# -----------------------------

def get_coordinates(place):
    url = "https://nominatim.openstreetmap.org/search"

    headers = {
        "User-Agent": "UniteaaAI/1.0 contact: uniteaa@example.com"
    }

    params = {
        "q": place,
        "format": "json",
        "limit": 1
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=15
    )

    response.raise_for_status()
    results = response.json()

    if not results:
        return None

    return (
        float(results[0]["lon"]),
        float(results[0]["lat"])
    )


def calculate_distance(place1, place2):
    start = get_coordinates(place1)
    end = get_coordinates(place2)

    if not start or not end:
        return None

    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start[0]},{start[1]};{end[0]},{end[1]}"
    )

    params = {
        "overview": "false"
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        return None

    distance_km = data["routes"][0]["distance"] / 1000

    return round(distance_km, 2)


def distance_answer(message):
    patterns = [
        r"distance\s+between\s+(.+?)\s+and\s+(.+)",
        r"how\s+far\s+is\s+(.+?)\s+from\s+(.+)"
    ]

    for index, pattern in enumerate(patterns):
        match = re.search(pattern, message, re.IGNORECASE)

        if match:
            if index == 0:
                place1 = match.group(1).strip(" ?.,")
                place2 = match.group(2).strip(" ?.,")
            else:
                place1 = match.group(1).strip(" ?.,")
                place2 = match.group(2).strip(" ?.,")

            try:
                distance = calculate_distance(place1, place2)

                if distance is None:
                    return (
                        "Sorry, I couldn't find the route. "
                        "Please check the place names and try again."
                    )

                return (
                    f"The approximate driving distance between "
                    f"{place1} and {place2} is {distance} km."
                )

            except requests.RequestException:
                return (
                    "The distance service is temporarily unavailable. "
                    "Please try again later."
                )
            except (ValueError, KeyError, TypeError):
                return "Sorry, I couldn't calculate that distance."

    return None


# -----------------------------
# GEMINI AI
# -----------------------------

def ask_gemini(message, history):
    if not GEMINI_API_KEY:
        raise Exception("Gemini API key is not configured.")

    contents = []

    for item in history[-10:]:
        role = item.get("role", "user")
        content = item.get("content", "")

        if not content:
            continue

        gemini_role = "model" if role == "assistant" else "user"

        contents.append({
            "role": gemini_role,
            "parts": [{"text": str(content)}]
        })

    contents.append({
        "role": "user",
        "parts": [{"text": message}]
    })

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    response = requests.post(
        GEMINI_URL,
        params={"key": GEMINI_API_KEY},
        json=payload,
        timeout=45
    )

    if not response.ok:
        try:
            error_info = response.json().get("error", {}).get(
                "message", response.text
            )
        except ValueError:
            error_info = response.text

        raise Exception(
            f"Gemini HTTP {response.status_code}: {error_info}"
        )

    data = response.json()

    candidates = data.get("candidates", [])

    if not candidates:
        raise Exception("Gemini returned no answer.")

    parts = candidates[0].get("content", {}).get("parts", [])

    answer = "".join(
        part.get("text", "") for part in parts
    ).strip()

    if not answer:
        raise Exception("Gemini returned an empty answer.")

    return answer


# -----------------------------
# GROQ BACKUP AI
# -----------------------------

def ask_groq(message, history):
    if not GROQ_API_KEY:
        raise Exception("Groq API key is not configured.")

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    for item in history[-10:]:
        role = item.get("role", "user")

        if role not in ("user", "assistant"):
            continue

        content = item.get("content", "")

        if content:
            messages.append({
                "role": role,
                "content": str(content)
            })

    messages.append({
        "role": "user",
        "content": message
    })

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_completion_tokens": 1800
    }

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=45
    )

    if not response.ok:
        try:
            error_info = response.json().get("error", {}).get(
                "message", response.text
            )
        except ValueError:
            error_info = response.text

        raise Exception(
            f"Groq HTTP {response.status_code}: {error_info}"
        )

    data = response.json()

    choices = data.get("choices", [])

    if not choices:
        raise Exception("Groq returned no answer.")

    answer = choices[0].get("message", {}).get("content", "")

    if not answer:
        raise Exception("Groq returned an empty answer.")

    return answer.strip()


# -----------------------------
# CHAT API
# -----------------------------

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}

    message = str(data.get("message", "")).strip()
    history = data.get("history", [])

    if not message:
        return jsonify({
            "reply": "Please enter a message.",
            "answer": "Please enter a message."
        }), 400

    if not isinstance(history, list):
        history = []

    # Creator information
    special_answer = creator_answer(message)

    if special_answer:
        return jsonify({
            "reply": special_answer,
            "answer": special_answer,
            "provider": "Uniteaa AI"
        })

    # Distance calculator
    distance_result = distance_answer(message)

    if distance_result:
        return jsonify({
            "reply": distance_result,
            "answer": distance_result,
            "provider": "Distance Calculator"
        })

    # Try Gemini first
    gemini_error = None

    if GEMINI_API_KEY:
        try:
            answer = ask_gemini(message, history)

            return jsonify({
                "reply": answer,
                "answer": answer,
                "provider": "Gemini"
            })

        except Exception as error:
            gemini_error = str(error)
            app.logger.error("Gemini failed: %s", gemini_error)

    else:
        gemini_error = "Gemini API key is not configured."

    # Automatically try Groq if Gemini fails
    if GROQ_API_KEY:
        try:
            answer = ask_groq(message, history)

            return jsonify({
                "reply": answer,
                "answer": answer,
                "provider": "Groq",
                "fallback": True
            })

        except Exception as groq_error:
            app.logger.error("Groq failed: %s", str(groq_error))

            return jsonify({
                "reply": (
                    "Both AI services are temporarily unavailable. "
                    "Please try again later."
                ),
                "answer": (
                    "Both AI services are temporarily unavailable. "
                    "Please try again later."
                ),
                "error": "Both AI providers failed."
            }), 503

    return jsonify({
        "reply": (
            "The AI service is currently unavailable. "
            "Please check the API configuration and try again."
        ),
        "answer": (
            "The AI service is currently unavailable. "
            "Please check the API configuration and try again."
        ),
        "error": gemini_error
    }), 503


# -----------------------------
# HEALTH CHECK
# -----------------------------

@app.route("/health")
def health():
    return jsonify({
        "assistant": ASSISTANT_NAME,
        "creator": CREATOR_NAME,
        "status": "online",
        "gemini_configured": bool(GEMINI_API_KEY),
        "groq_configured": bool(GROQ_API_KEY),
        "primary_model": GEMINI_MODEL,
        "backup_model": GROQ_MODEL,
        "distance_calculator": "enabled",
        "distance_provider": "OpenStreetMap and OSRM",
        "live_web_search": "disabled",
        "voice_dictation": "disabled",
        "file_input_output": "disabled"
    })


# -----------------------------
# SITEMAP
# -----------------------------

@app.route("/sitemap.xml")
def sitemap():
    return """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://florence-ai.onrender.com/</loc>
    </url>
</urlset>
""", 200, {"Content-Type": "application/xml"}


# -----------------------------
# ROBOTS.TXT
# -----------------------------

@app.route("/robots.txt")
def robots():
    return """User-agent: *
Allow: /

Sitemap: https://florence-ai.onrender.com/sitemap.xml
""", 200, {"Content-Type": "text/plain"}


# -----------------------------
# GOOGLE VERIFICATION
# -----------------------------

@app.route("/google727dc86e6654fcd4.html")
def google_verification():
    return (
        "google-site-verification: google727dc86e6654fcd4.html",
        200,
        {"Content-Type": "text/html"}
    )


# -----------------------------
# START APPLICATION
# -----------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
