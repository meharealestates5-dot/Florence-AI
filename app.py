
import os
import io
import re
import csv
import json
import base64
import logging
from datetime import datetime
from urllib.parse import urlparse

import requests
from flask import (
    Flask, render_template, request, jsonify,
    send_file
)
from werkzeug.utils import secure_filename

from pypdf import PdfReader
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "uniteaa-ai-secret")

app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uniteaa")

ASSISTANT_NAME = "Uniteaa AI"
CREATOR_NAME = "M. Mehanethra"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TAVILY_URL = "https://api.tavily.com/search"

ALLOWED_EXTENSIONS = {
    "pdf", "docx", "txt", "csv", "md", "json",
    "png", "jpg", "jpeg", "webp"
}

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


# --------------------------------------------------
# GENERAL HELPERS
# --------------------------------------------------

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def file_extension(filename):
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def clean_text(value):
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()


def is_quota_error(message):
    value = str(message).lower()
    phrases = [
        "429", "quota", "rate limit", "rate_limit",
        "insufficient_quota", "credit_balance_exhausted",
        "billing", "resource_exhausted",
        "exceeded your current quota",
        "tokens per minute", "requests per minute"
    ]
    return any(term in value for term in phrases)


def is_live_question(question):
    q = question.lower()

    keywords = [
        "today", "current", "currently", "latest", "recent",
        "now", "right now", "live", "this week", "this month",
        "president", "prime minister", "chief minister",
        "chief ministers", "gold rate", "silver rate",
        "gold price", "silver price", "thangam", "velli",
        "weather", "news", "election", "result",
        "stock price", "exchange rate", "petrol price",
        "diesel price", "who is the", "how much is"
    ]

    return any(word in q for word in keywords)


# --------------------------------------------------
# FILE EXTRACTION
# --------------------------------------------------

def extract_file_text(file):
    filename = secure_filename(file.filename or "upload")
    ext = file_extension(filename)

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported file type.")

    raw = file.read()
    if not raw:
        return "", None

    if ext == "pdf":
        reader = PdfReader(io.BytesIO(raw))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages), None

    if ext == "docx":
        doc = Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]

        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))

        return "\n".join(parts), None

    if ext in {"txt", "csv", "md", "json"}:
        try:
            return raw.decode("utf-8-sig"), None
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace"), None

    if ext in IMAGE_EXTENSIONS:
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp"
        }[ext]
        return "", {
            "mime_type": mime,
            "data": base64.b64encode(raw).decode("utf-8")
        }

    return "", None


# --------------------------------------------------
# LIVE WEB SEARCH
# --------------------------------------------------

def get_search_queries(question):
    q = question.strip()
    lower = q.lower()

    if any(x in lower for x in [
        "gold rate", "gold price", "thangam"
    ]):
        return [
            f"Chennai Tamil Nadu gold rate today 22 carat 24 carat per gram INR {q}",
            "Chennai today 22K 24K gold price per gram India",
            "Tamil Nadu gold rate today reliable source"
        ]

    if any(x in lower for x in [
        "chief minister", "chief ministers", "all cm"
    ]):
        return [
            "current Chief Ministers of all 28 Indian states official updated list",
            "India current state Chief Ministers 2026 official sources",
            "current Chief Ministers Delhi Jammu Kashmir Puducherry"
        ]

    if "president" in lower and (
        "united states" in lower or " usa" in lower or "american" in lower
    ):
        return [
            "current President of the United States official White House",
            "White House President of the United States current"
        ]

    if "prime minister" in lower and (
        "india" in lower or "indian" in lower
    ):
        return [
            "current Prime Minister of India official government website",
            "Prime Minister of India current official"
        ]

    return [
        q,
        f"{q} latest information official source",
        f"{q} current update India"
    ]


def tavily_search(query):
    if not TAVILY_API_KEY:
        return []

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "topic": "general",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False
    }

    response = requests.post(
        TAVILY_URL,
        json=payload,
        timeout=20
    )
    response.raise_for_status()

    data = response.json()
    results = []

    for item in data.get("results", []):
        results.append({
            "title": clean_text(item.get("title")),
            "url": clean_text(item.get("url")),
            "content": clean_text(item.get("content")),
            "source": "Tavily"
        })

    return results


def duckduckgo_search(query):
    if DDGS is None:
        return []

    results = []

    try:
        with DDGS() as ddgs:
            found = ddgs.text(
                query,
                region="in-en",
                safesearch="moderate",
                max_results=5
            )

            for item in found or []:
                results.append({
                    "title": clean_text(item.get("title")),
                    "url": clean_text(item.get("href")),
                    "content": clean_text(item.get("body")),
                    "source": "DuckDuckGo"
                })

    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)

    return results


def search_web(question):
    queries = get_search_queries(question)
    collected = []
    seen = set()

    # Try Tavily first.
    if TAVILY_API_KEY:
        for query in queries:
            try:
                results = tavily_search(query)
                logger.info(
                    "Tavily returned %s results for: %s",
                    len(results), query
                )

                for item in results:
                    url = item.get("url", "")
                    if url and url not in seen:
                        seen.add(url)
                        collected.append(item)

            except Exception as exc:
                logger.warning("Tavily search failed: %s", exc)

    # Use DuckDuckGo if Tavily has no usable results.
    if not collected:
        logger.info("Using DuckDuckGo backup search.")

        for query in queries:
            results = duckduckgo_search(query)

            for item in results:
                url = item.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    collected.append(item)

    return collected[:15]


def format_search_context(results):
    if not results:
        return "No verified live search results were retrieved."

    parts = []

    for i, item in enumerate(results, start=1):
        parts.append(
            f"Source {i}\n"
            f"Title: {item.get('title', '')}\n"
            f"URL: {item.get('url', '')}\n"
            f"Information: {item.get('content', '')}\n"
        )

    return "\n".join(parts)


# --------------------------------------------------
# AI PROVIDERS
# --------------------------------------------------

def ask_gemini(prompt, image_data=None):
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key is not configured.")

    parts = [{"text": prompt}]

    if image_data:
        parts.append({
            "inline_data": {
                "mime_type": image_data["mime_type"],
                "data": image_data["data"]
            }
        })

    response = requests.post(
        GEMINI_URL,
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 4096
            }
        },
        timeout=90
    )

    if not response.ok:
        raise RuntimeError(
            f"Gemini API {response.status_code}: {response.text[:1200]}"
        )

    data = response.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no answer.")

    answer_parts = (
        candidates[0].get("content", {}).get("parts", [])
    )

    answer = "\n".join(
        p.get("text", "") for p in answer_parts if p.get("text")
    ).strip()

    if not answer:
        raise RuntimeError("Gemini returned an empty answer.")

    return answer


def ask_openai(prompt):
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI API key is not configured.")

    response = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": OPENAI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Uniteaa AI. Answer accurately and clearly. "
                        "Use supplied live search results when available."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            "max_completion_tokens": 4096
        },
        timeout=90
    )

    if not response.ok:
        raise RuntimeError(
            f"OpenAI API {response.status_code}: {response.text[:1200]}"
        )

    data = response.json()
    answer = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

    if not answer:
        raise RuntimeError("OpenAI returned an empty answer.")

    return answer.strip()


def ask_groq(prompt):
    if not GROQ_API_KEY:
        raise RuntimeError("Groq API key is not configured.")

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Uniteaa AI. Answer accurately and clearly. "
                        "Use supplied live search results when available. "
                        "Never invent current prices, dates, or names."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.4,
            "max_tokens": 4096
        },
        timeout=90
    )

    if not response.ok:
        raise RuntimeError(
            f"Groq API {response.status_code}: {response.text[:1200]}"
        )

    data = response.json()
    answer = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

    if not answer:
        raise RuntimeError("Groq returned an empty answer.")

    return answer.strip()


# --------------------------------------------------
# ANSWER GENERATION AND PROVIDER FALLBACK
# --------------------------------------------------

def build_prompt(question, search_context, file_text):
    return f"""
You are {ASSISTANT_NAME}, created by {CREATOR_NAME}.

Answer the user's question directly, clearly, and helpfully.

IMPORTANT LIVE INFORMATION RULES:
1. Use the live search results supplied below when relevant.
2. Do not invent current information, prices, dates, or government leaders.
3. For gold rates, state the 22K and 24K rates per gram only when the sources provide them.
4. Mention the date or update time if the source provides it.
5. For Indian Chief Ministers, distinguish states from Union Territories.
6. If sources disagree, explain the disagreement.
7. If live search results are missing, say that you could not verify the current information.
8. Include useful source URLs in your answer.
9. Do not pretend that a search succeeded if no results were returned.
10. Answer ordinary knowledge questions normally.

USER QUESTION:
{question}

LIVE SEARCH RESULTS:
{search_context}

UPLOADED FILE CONTENT:
{file_text[:25000] if file_text else "No text file uploaded."}

Now answer the user.
"""


def generate_answer(prompt, image_data=None):
    providers = [
        ("Gemini", lambda: ask_gemini(prompt, image_data)),
        ("OpenAI", lambda: ask_openai(prompt)),
        ("Groq", lambda: ask_groq(prompt))
    ]

    errors = []

    for provider_name, provider_call in providers:
        try:
            logger.info("Trying AI provider: %s", provider_name)

            # OpenAI and Groq text endpoints are not used for image uploads.
            if image_data and provider_name != "Gemini":
                logger.info(
                    "Skipping %s because this request contains an image.",
                    provider_name
                )
                continue

            answer = provider_call()

            if answer and answer.strip():
                logger.info(
                    "AI response generated by %s", provider_name
                )
                return answer.strip(), provider_name

        except Exception as exc:
            message = str(exc)
            errors.append(f"{provider_name}: {message}")

            if is_quota_error(message):
                logger.warning(
                    "%s quota unavailable; trying next provider.",
                    provider_name
                )
            else:
                logger.warning(
                    "%s provider failed: %s",
                    provider_name, message
                )

    raise RuntimeError(
        "All configured AI providers failed. " +
        " | ".join(errors)[-3500:]
    )


# --------------------------------------------------
# ROUTES
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "online",
        "assistant": ASSISTANT_NAME,
        "creator": CREATOR_NAME,
        "provider_order": ["Gemini", "OpenAI", "Groq"],
        "primary_model": GEMINI_MODEL,
        "openai_model": OPENAI_MODEL,
        "backup_model": GROQ_MODEL,
        "gemini_configured": bool(GEMINI_API_KEY),
        "openai_configured": bool(OPENAI_API_KEY),
        "groq_configured": bool(GROQ_API_KEY),
        "tavily_configured": bool(TAVILY_API_KEY),
        "duckduckgo_backup": DDGS is not None,
        "live_web_search": True,
        "image_generation": False,
        "file_upload": True,
        "file_export": True
    })


@app.route("/chat", methods=["POST"])
def chat():
    try:
        message = clean_text(request.form.get("message", ""))

        uploaded_file = request.files.get("file")
        file_text = ""
        image_data = None

        if uploaded_file and uploaded_file.filename:
            if not allowed_file(uploaded_file.filename):
                return jsonify({
                    "error": "Unsupported file type."
                }), 400

            file_text, image_data = extract_file_text(uploaded_file)

        if not message and not file_text and not image_data:
            return jsonify({
                "error": "Please enter a message or upload a file."
            }), 400

        if not message:
            message = "Please explain or summarize the uploaded file."

        live_enabled = (
            request.form.get("live_search", "true").lower()
            in {"true", "1", "yes", "on"}
        )

        should_search = live_enabled and is_live_question(message)

        search_results = []
        search_context = "Live search was not requested."

        if should_search:
            search_results = search_web(message)
            search_context = format_search_context(search_results)

        prompt = build_prompt(
            message,
            search_context,
            file_text
        )

        answer, provider = generate_answer(prompt, image_data)

        sources = []
        for item in search_results:
            url = item.get("url", "")
            if url:
                sources.append({
                    "title": item.get("title", ""),
                    "url": url
                })

        return jsonify({
            "answer": answer,
            "reply": answer,
            "provider": provider,
            "live_search_used": bool(search_results),
            "search_attempted": should_search,
            "search_result_count": len(search_results),
            "sources": sources
        })

    except Exception as exc:
        logger.exception("Chat request failed.")
        return jsonify({
            "error": str(exc)
        }), 503


# --------------------------------------------------
# ANSWER EXPORT
# --------------------------------------------------

def get_answer_from_request():
    data = request.get_json(silent=True) or {}
    answer = clean_text(data.get("answer", ""))

    if not answer:
        raise ValueError("There is no answer to export.")

    return answer


@app.route("/download", methods=["POST"])
def download_txt():
    try:
        answer = get_answer_from_request()
        stream = io.BytesIO(answer.encode("utf-8"))
        stream.seek(0)

        return send_file(
            stream,
            as_attachment=True,
            download_name="uniteaa_ai_answer.txt",
            mimetype="text/plain"
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/export/<file_format>", methods=["POST"])
def export_answer(file_format):
    try:
        answer = get_answer_from_request()
        file_format = file_format.lower()

        if file_format == "txt":
            stream = io.BytesIO(answer.encode("utf-8"))
            mimetype = "text/plain"
            filename = "uniteaa_ai_answer.txt"

        elif file_format == "docx":
            doc = Document()
            doc.add_heading("Uniteaa AI Answer", 0)

            for line in answer.splitlines():
                doc.add_paragraph(line)

            stream = io.BytesIO()
            doc.save(stream)
            stream.seek(0)

            mimetype = (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            )
            filename = "uniteaa_ai_answer.docx"

        elif file_format == "pdf":
            stream = io.BytesIO()
            pdf = SimpleDocTemplate(
                stream,
                pagesize=A4,
                rightMargin=40,
                leftMargin=40,
                topMargin=40,
                bottomMargin=40
            )

            styles = getSampleStyleSheet()
            story = [
                Paragraph("Uniteaa AI Answer", styles["Title"]),
                Spacer(1, 16)
            ]

            for line in answer.splitlines():
                safe_line = (
                    line.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                story.append(
                    Paragraph(safe_line or " ", styles["BodyText"])
                )
                story.append(Spacer(1, 6))

            pdf.build(story)
            stream.seek(0)

            mimetype = "application/pdf"
            filename = "uniteaa_ai_answer.pdf"

        else:
            return jsonify({
                "error": "Unsupported export format."
            }), 400

        return send_file(
            stream,
            as_attachment=True,
            download_name=filename,
            mimetype=mimetype
        )

    except Exception as exc:
        logger.exception("Export failed.")
        return jsonify({"error": str(exc)}), 400


# --------------------------------------------------
# ERROR HANDLERS
# --------------------------------------------------

@app.errorhandler(413)
def file_too_large(error):
    return jsonify({
        "error": "File is too large. Maximum upload size is 20 MB."
    }), 413


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "The requested page was not found."
    }), 404


@app.errorhandler(500)
def server_error(error):
    logger.exception("Internal server error.")
    return jsonify({
        "error": "Internal server error. Check Render logs."
    }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
