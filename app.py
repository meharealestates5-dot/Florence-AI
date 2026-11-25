
import os
import time
import math
import re
import threading
import requests

from datetime import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache
from flask import Flask, render_template, request, jsonify, Response
from ddgs import DDGS

app = Flask(__name__)

# ==========================================
# FLORENCE AI CONFIGURATION
# ==========================================

MODEL = "gemini-3.8-flash"
GROQ_MODEL = "openai/gpt-oss-120b"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/"
    f"v1beta/models/{MODEL}:generateContent"
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

WEBSITE_URL = "https://florence-ai-1.onrender.com"

print("Gemini configured:", bool(GEMINI_API_KEY))
print("Groq configured:", bool(GROQ_API_KEY))


def current_date():
    return datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).strftime("%Y-%m-%d")


# ==========================================
# SYSTEM PROMPT
# ==========================================

SYSTEM_PROMPT = """
You are Florence AI, a helpful general-purpose AI assistant.

Your creator is M. Mehanethra.

You must identify yourself as Florence AI.
Never claim that you are ChatGPT or another assistant.

YOUR RESPONSIBILITIES:

1. Answer questions from all subjects.
2. Help with mathematics and calculations.
3. Explain science and technology.
4. Answer history and geography questions.
5. Help with computer programming.
6. Help with education and general knowledge.
7. Support creative writing and brainstorming.
8. Communicate in simple, understandable language.
9. Give accurate, direct and useful answers.
10. Admit when you are unsure instead of inventing facts.

CURRENT INFORMATION RULES:

1. When internet search results are supplied, use them
   as the primary evidence for current facts.

2. Never answer a current-information question using
   outdated stored knowledge when newer evidence is supplied.

3. Check publication dates when available.

4. Prefer official government websites, official
   announcements and reliable news organisations.

5. Never invent election results, dates, names,
   prices, news or other facts.

6. Distinguish confirmed facts from unverified reports.

7. Cite sources using their source numbers, such as [1] or [2].

8. If search results do not establish the answer,
   clearly say that the information could not be verified.

9. Do not claim that information is current unless
   the available evidence supports that claim.

10. Never treat an old search result as proof of a
    current position or office.

11. The current date will be provided in the conversation.
    Use it when interpreting current-information questions.

GENERAL ANSWERING RULES:

For mathematics, show calculations when useful.
For programming, provide working code and explanations.
Keep simple answers concise.

For geographical distances, use verified distance data
when available. Never invent road distances.

You are Florence AI, created by M. Mehanethra.
"""


# ==========================================
# GOOGLE SITEMAP
# ==========================================

@app.route("/sitemap.xml")
def sitemap():

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{WEBSITE_URL}/</loc>
        <changefreq>weekly</changefreq>
        <priority>1.0</priority>
    </url>
</urlset>
"""

    return Response(xml, mimetype="application/xml")


# ==========================================
# ROBOTS.TXT
# ==========================================

@app.route("/robots.txt")
def robots():

    content = f"""User-agent: *
Allow: /

Sitemap: {WEBSITE_URL}/sitemap.xml
"""

    return Response(content, mimetype="text/plain")


# ==========================================
# INTERNET SEARCH
# ==========================================

def search_web(query):

    try:

        today = current_date()

        search_query = (
            f"{query} latest information "
            f"{today}"
        )

        print("DuckDuckGo search:", search_query)

        with DDGS() as ddgs:

            results = list(
                ddgs.text(
                    search_query,
                    max_results=6
                )
            )

        information = []

        for index, item in enumerate(results, 1):

            information.append(
                f"Source ID: {index}\n"
                f"Title: {item.get('title', '')}\n"
                f"Information: {item.get('body', '')}\n"
                f"Date: {item.get('date', 'Not provided')}\n"
                f"Source URL: {item.get('href', '')}"
            )

        return "\n\n".join(information)

    except Exception as error:

        print("Web search error:", str(error))

        return ""


# ==========================================
# FORMAT SOURCE LINKS
# ==========================================

def format_sources(web_information):

    sources = []
    seen = set()

    for block in web_information.split("\n\n"):

        title = ""
        url = ""

        for line in block.splitlines():

            if line.startswith("Title:"):

                title = line.replace(
                    "Title:", "", 1
                ).strip()

            elif line.startswith("Source URL:"):

                url = line.replace(
                    "Source URL:", "", 1
                ).strip()

        if (
            url.startswith(("https://", "http://"))
            and url not in seen
        ):

            seen.add(url)

            safe_title = (
                title.replace("[", "(")
                .replace("]", ")")
            )

            sources.append(
                f"- [{safe_title or url}]({url})"
            )

    if not sources:

        return ""

    return (
        "\n\n### Sources\n"
        + "\n".join(sources)
    )


# ==========================================
# DETECT CURRENT INFORMATION QUESTIONS
# ==========================================

def needs_search(message):

    message = message.lower().strip()

    patterns = [

        # Current and recent information

        r"\b(latest|currently|current|today|now|recent)\b",

        r"\b(this week|this month|this year)\b",

        r"\b(news|headlines|breaking news|live updates)\b",

        r"\b(weather|forecast|traffic now)\b",

        # Prices and changing information

        r"\b(price today|stock price|gold rate|silver rate)\b",

        r"\b(petrol price|diesel price|exchange rate)\b",

        r"\b(current price|latest price|today's price)\b",

        # Government and political office holders

        r"\b(chief minister|prime minister|president|"
        r"governor|minister|mayor)\b",

        r"\b(current government|present government)\b",

        # Elections

        r"\b(who won|winner|election result|election results)\b",

        r"\b(assembly election|general election|vote count)\b",

        r"\b(election results|election news)\b",

        # Recent events

        r"\b(what happened|what is happening)\b",

        r"\b(latest update|recent update|new announcement)\b",

        r"\b(is .+ still|has .+ changed|when will .+ happen)\b",

        # Current leadership questions

        r"\b(who is|who's|name the)\b.*"
        r"\b(chief minister|prime minister|president|"
        r"governor|minister|leader|mayor)\b",

        # Specific current-year events

        r"\b(2026|2027)\b.*"
        r"\b(election|result|news|update|winner)\b"

    ]

    return any(
        re.search(pattern, message)
        for pattern in patterns
    )


# ==========================================
# FLORENCE AI DISTANCE SYSTEM
# ==========================================

NOMINATIM_URL = (
    "https://nominatim.openstreetmap.org/search"
)

OSRM_URL = (
    "https://router.project-osrm.org/route/v1/driving/"
)

NOMINATIM_HEADERS = {
    "User-Agent": "FlorenceAI/1.0"
}

nominatim_lock = threading.Lock()
last_nominatim_request = 0


def extract_distance_places(message):

    text = re.sub(
        r"\s+",
        " ",
        message.strip()
    )

    patterns = [

        r"\bdistance\s+between\s+(.+?)\s+(?:and|to)\s+(.+)",

        r"\bdistance\s+from\s+(.+?)\s+to\s+(.+)",

        r"\bwhat\s+is\s+(?:the\s+)?distance\s+between\s+(.+?)\s+(?:and|to)\s+(.+)",

        r"\bhow\s+far\s+is\s+(.+?)\s+from\s+(.+)",

        r"\bhow\s+far\s+from\s+(.+?)\s+to\s+(.+)",

        r"^(.+?)\s+to\s+(.+?)\s+distance$"

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            place1 = clean_place_name(
                match.group(1)
            )

            place2 = clean_place_name(
                match.group(2)
            )

            if place1 and place2:

                return place1, place2

    return None


def clean_place_name(place):

    place = place.strip()

    place = re.sub(
        r"^(?:please\s+)?(?:tell\s+me\s+)?",
        "",
        place,
        flags=re.IGNORECASE
    )

    place = re.sub(
        r"\s+(?:in\s+km|in\s+kilometers|in\s+kilometres)$",
        "",
        place,
        flags=re.IGNORECASE
    )

    return place.strip(" .,?!")


@lru_cache(maxsize=256)
def geocode_place(place):

    global last_nominatim_request

    known_countries = [
        "india", "usa", "united states", "uk",
        "united kingdom", "canada", "australia",
        "singapore", "uae", "united arab emirates",
        "france", "germany", "japan", "nepal",
        "sri lanka", "bangladesh", "new zealand"
    ]

    has_country = any(
        country in place.lower()
        for country in known_countries
    )

    params = {
        "q": place,
        "format": "jsonv2",
        "limit": 1
    }

    if not has_country:

        params["countrycodes"] = "in"
        params["q"] = place + ", India"

    with nominatim_lock:

        wait_time = (
            1.1
            - (time.monotonic() - last_nominatim_request)
        )

        if wait_time > 0:

            time.sleep(wait_time)

        last_nominatim_request = time.monotonic()

    response = requests.get(
        NOMINATIM_URL,
        params=params,
        headers=NOMINATIM_HEADERS,
        timeout=20
    )

    response.raise_for_status()

    results = response.json()

    if not results:

        raise ValueError(
            "Location not found: " + place
        )

    location = results[0]

    return {
        "lat": float(location["lat"]),
        "lon": float(location["lon"]),
        "name": location["display_name"]
    }


def calculate_straight_distance(place1, place2):

    earth_radius = 6371.0

    lat1 = math.radians(place1["lat"])
    lat2 = math.radians(place2["lat"])

    delta_lat = math.radians(
        place2["lat"] - place1["lat"]
    )

    delta_lon = math.radians(
        place2["lon"] - place1["lon"]
    )

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    a = max(0.0, min(1.0, a))

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius * c


def calculate_road_distance(place1, place2):

    url = (
        OSRM_URL
        + f"{place1['lon']},{place1['lat']};"
        + f"{place2['lon']},{place2['lat']}"
    )

    response = requests.get(
        url,
        params={
            "overview": "false",
            "steps": "false",
            "alternatives": "false"
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if (
        result.get("code") != "Ok"
        or not result.get("routes")
    ):

        raise ValueError("Driving route unavailable")

    route = result["routes"][0]

    road_km = route["distance"] / 1000

    duration_minutes = route["duration"] / 60

    return road_km, duration_minutes


def format_travel_time(minutes):

    total_minutes = round(minutes)

    hours = total_minutes // 60
    remaining_minutes = total_minutes % 60

    if hours:

        return (
            f"{hours} hour(s) "
            f"{remaining_minutes} minute(s)"
        )

    return f"{remaining_minutes} minute(s)"


def get_distance_answer(message):

    places = extract_distance_places(message)

    if not places:

        return None

    name1, name2 = places

    try:

        print("Distance request:", name1, "to", name2)

        place1 = geocode_place(name1)
        place2 = geocode_place(name2)

        straight_distance = calculate_straight_distance(
            place1,
            place2
        )

        try:

            road_distance, travel_minutes = (
                calculate_road_distance(
                    place1,
                    place2
                )
            )

            travel_time = format_travel_time(
                travel_minutes
            )

            answer = f"""
## Distance from {name1} to {name2}

| Measure | Approximate value |
|---|---:|
| Road distance (driving) | {road_distance:.1f} km |
| Straight-line distance | {straight_distance:.1f} km |
| Estimated driving time | {travel_time} |

**Data:** OpenStreetMap and OSRM.

**Important:** Travel time does not include live traffic.
Actual distance can vary depending on exact locations
and the selected route.
"""

        except Exception as route_error:

            print(
                "Road routing unavailable:",
                str(route_error)
            )

            answer = f"""
## Distance from {name1} to {name2}

**Straight-line distance:** {straight_distance:.1f} km

**Road distance:** Currently unavailable.

I cannot verify the driving distance right now,
so I will not guess it.
"""

        return answer.strip()

    except Exception as error:

        print("Distance calculation error:", str(error))

        return (
            f"I could not verify the distance between "
            f"{name1} and {name2} right now. "
            "Please check the spelling or try again later."
        )


# ==========================================
# PREPARE CONVERSATION HISTORY
# ==========================================

def prepare_history(history):

    contents = []

    for item in history[-8:]:

        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")

        if (
            role in ["user", "assistant"]
            and isinstance(content, str)
            and content.strip()
        ):

            contents.append({

                "role": (
                    "model"
                    if role == "assistant"
                    else "user"
                ),

                "parts": [{
                    "text": content[:4000]
                }]

            })

    merged = []

    for item in contents:

        if (
            merged
            and merged[-1]["role"] == item["role"]
        ):

            merged[-1]["parts"][0]["text"] += (
                "\n\n" + item["parts"][0]["text"]
            )

        else:

            merged.append(item)

    return merged


# ==========================================
# GEMINI CONNECTION WITH RETRIES
# ==========================================

def connect_gemini(payload):

    retry_codes = [429, 500, 502, 503, 504]

    for attempt in range(3):

        response = requests.post(
            GEMINI_URL,

            headers={
                "x-goog-api-key": GEMINI_API_KEY,
                "Content-Type": "application/json"
            },

            json=payload,

            timeout=60
        )

        if response.status_code in retry_codes:

            print(
                "Gemini temporary error:",
                response.status_code
            )

            if attempt < 2:

                time.sleep(2 ** attempt)
                continue

        response.raise_for_status()

        return response

    raise RuntimeError(
        "Gemini unavailable after retries"
    )


# ==========================================
# GEMINI PRIMARY AI
# ==========================================

def ask_gemini(contents):

    dated_prompt = (
        SYSTEM_PROMPT
        + "\n\nCURRENT DATE IN INDIA: "
        + current_date()
    )

    payload = {

        "systemInstruction": {
            "parts": [{
                "text": dated_prompt
            }]
        },

        "contents": contents,

        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 1500
        }

    }

    response = connect_gemini(payload)

    result = response.json()

    candidates = result.get("candidates", [])

    if not candidates:

        raise RuntimeError(
            "Gemini returned no candidates: "
            + str(result)[:1000]
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    answer = "\n".join(
        part.get("text", "")
        for part in parts
        if isinstance(part.get("text"), str)
    ).strip()

    if not answer:

        raise RuntimeError(
            "Gemini returned an empty answer"
        )

    return answer


# ==========================================
# GROQ BACKUP AI
# ==========================================

def ask_groq(history, message, web_information):

    if not GROQ_API_KEY:

        raise RuntimeError(
            "Groq API key is missing"
        )

    dated_prompt = (
        SYSTEM_PROMPT
        + "\n\nCURRENT DATE IN INDIA: "
        + current_date()
    )

    messages = [

        {
            "role": "system",
            "content": dated_prompt
        }

    ]

    for item in history[-8:]:

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

    current_message = message

    if web_information:

        current_message = (
            "IMPORTANT: Use these internet search results "
            "as your primary evidence for current facts.\n\n"
            "Today's date: "
            + current_date()
            + "\n\n"
            + web_information[:12000]
            + "\n\n"
            + "Do not invent facts or dates. "
            + "If evidence is insufficient, say so.\n\n"
            + "USER QUESTION:\n"
            + message
        )

    messages.append({

        "role": "user",
        "content": current_message

    })

    payload = {

        "model": GROQ_MODEL,

        "messages": messages,

        "temperature": 0.4,

        "max_completion_tokens": 1500

    }

    response = requests.post(
        GROQ_URL,

        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },

        json=payload,

        timeout=60
    )

    if not response.ok:

        print(
            "Groq HTTP status:",
            response.status_code
        )

        print(
            "Groq error:",
            response.text[:2000]
        )

    response.raise_for_status()

    result = response.json()

    choices = result.get("choices", [])

    if not choices:

        raise RuntimeError(
            "Groq returned no choices"
        )

    answer = (
        choices[0]
        .get("message", {})
        .get("content", "")
    )

    if (
        not isinstance(answer, str)
        or not answer.strip()
    ):

        raise RuntimeError(
            "Groq returned an empty answer"
        )

    return answer.strip()


# ==========================================
# HOME PAGE
# ==========================================

@app.route("/")
def home():

    return render_template("index.html")


# ==========================================
# GOOGLE VERIFICATION
# ==========================================

@app.route("/google727dc86e6654fcd4.html")
def google_verification():

    return (
        "google-site-verification: google727dc86e6654fcd4.html",
        200,
        {"Content-Type": "text/html"}
    )


# ==========================================
# STATUS CHECK
# ==========================================

@app.route("/health")
def health():

    return jsonify({

        "status": "online",

        "assistant": "Florence AI",

        "model": MODEL,

        "backup_model": GROQ_MODEL,

        "creator": "M. Mehanethra",

        "ai_configured": bool(
            GEMINI_API_KEY or GROQ_API_KEY
        ),

        "gemini_configured": bool(
            GEMINI_API_KEY
        ),

        "groq_configured": bool(
            GROQ_API_KEY
        ),

        "web_search": "enabled",

        "distance_calculator": "enabled",

        "distance_provider": "OpenStreetMap and OSRM"

    })


# ==========================================
# CHAT SYSTEM
# ==========================================

@app.route("/chat", methods=["POST"])
def chat():

    try:

        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict):

            return jsonify({
                "reply": "Please send a valid question."
            }), 400

        message = data.get("message", "")

        if not isinstance(message, str):

            return jsonify({
                "reply": "Please enter a valid question."
            }), 400

        message = message.strip()

        if not message:

            return jsonify({
                "reply": "Please enter a question."
            }), 400

        # CREATOR IDENTIFICATION

        creator_questions = [
            "who created you",
            "who developed you",
            "who made you",
            "your creator",
            "who is your developer"
        ]

        if any(
            q in message.lower()
            for q in creator_questions
        ):

            return jsonify({

                "reply": (
                    "I am Florence AI, developed "
                    "by M. Mehanethra!"
                ),

                "assistant": "Florence AI",

                "provider": "Florence AI"

            })

        # DISTANCE CALCULATION

        distance_answer = get_distance_answer(message)

        if distance_answer is not None:

            return jsonify({

                "reply": distance_answer,

                "assistant": "Florence AI",

                "provider": "Distance Calculator"

            })

        # API CONFIGURATION CHECK

        if not GEMINI_API_KEY and not GROQ_API_KEY:

            return jsonify({

                "reply": (
                    "Florence AI is not configured yet. "
                    "Please check the server configuration."
                )

            }), 503

        # CONVERSATION HISTORY

        history = data.get("history", [])

        if not isinstance(history, list):

            history = []

        history = history[-8:]

        # INTERNET SEARCH

        web_information = ""

        search_required = needs_search(message)

        if search_required:

            print(
                "Current information requested:",
                message
            )

            web_information = search_web(message)

            # DO NOT USE OLD MODEL KNOWLEDGE
            # WHEN CURRENT SEARCH FAILS.

            if not web_information.strip():

                return jsonify({

                    "reply": (
                        "I cannot verify the latest information "
                        "right now because internet search is "
                        "unavailable or returned no results. "
                        "Please try again later."
                    ),

                    "assistant": "Florence AI",

                    "provider": "Web Search"

                }), 503

        # PREPARE GEMINI CONVERSATION

        contents = prepare_history(history)

        current_message = message

        if web_information:

            current_message = (
                "IMPORTANT: Answer using the following "
                "internet search evidence.\n\n"
                "Today's date: "
                + current_date()
                + "\n\n"
                + web_information[:12000]
                + "\n\n"
                + "Use recent and reliable sources. "
                + "Do not invent facts or dates. "
                + "If evidence is insufficient, say so.\n\n"
                + "USER QUESTION:\n"
                + message
            )

        contents.append({

            "role": "user",

            "parts": [{
                "text": current_message
            }]

        })

        # PRIMARY AI: GEMINI

        if GEMINI_API_KEY:

            try:

                print("Trying primary AI: Gemini")

                answer = ask_gemini(contents)

                if web_information:

                    answer += format_sources(
                        web_information
                    )

                print("Gemini answered successfully")

                return jsonify({

                    "reply": answer,

                    "assistant": "Florence AI",

                    "provider": "Gemini",

                    "web_search_used": bool(
                        web_information
                    )

                })

            except Exception as error:

                print(
                    "Gemini failed:",
                    str(error)
                )

                print("Switching to Groq backup")

        # BACKUP AI: GROQ

        if GROQ_API_KEY:

            try:

                print("Trying backup AI: Groq")

                answer = ask_groq(
                    history,
                    message,
                    web_information
                )

                if web_information:

                    answer += format_sources(
                        web_information
                    )

                print("Groq answered successfully")

                return jsonify({

                    "reply": answer,

                    "assistant": "Florence AI",

                    "provider": "Groq",

                    "web_search_used": bool(
                        web_information
                    )

                })

            except Exception as error:

                print(
                    "Groq backup failed:",
                    str(error)
                )

        return jsonify({

            "reply": (
                "Florence AI is temporarily unable to connect "
                "to its AI providers. Please try again shortly."
            ),

            "assistant": "Florence AI"

        }), 503

    except Exception as error:

        print(
            "Florence AI error:",
            str(error)
        )

        return jsonify({

            "reply": (
                "Something went wrong. Please try again."
            ),

            "assistant": "Florence AI"

        }), 500


# ==========================================
# START FLORENCE AI
# ==========================================

if __name__ == "__main__":

    print("--------------------------------")
    print("          FLORENCE AI")
    print("   Created by M. Mehanethra")
    print("   Gemini + Groq Backup")
    print("   Internet Search Enabled")
    print("   Distance Calculator Enabled")
    print("--------------------------------")

    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get("PORT", 5000)
        ),

        debug=False

    )
