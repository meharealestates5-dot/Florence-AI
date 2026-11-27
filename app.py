import os
import io
import re
import json
import logging
import requests

from datetime import datetime
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from ddgs import DDGS


# =========================================================
# APP CONFIGURATION
# =========================================================

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-in-render")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uniteaa")

ASSISTANT_NAME = "Uniteaa AI"
CREATOR = "M. Mehanethra"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_IMAGE_MODEL = os.getenv(
    "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "45"))
SEARCH_MAX_RESULTS = 5


# =========================================================
# GENERAL HELPERS
# =========================================================

def clean_text(value):
    return str(value or "").strip()


def error_text(response):
    try:
        data = response.json()
        error = data.get("error", {})
        if isinstance(error, dict):
            return str(error.get("message") or data)
        return str(error or data)
    except Exception:
        return response.text[:1200]


def is_quota_error(message):
    text = str(message).lower()
    markers = (
        "insufficient_quota",
        "credit_balance_exhausted",
        "no credits remaining",
        "exceeded your current quota",
        "billing hard limit",
        "quota exceeded",
    )
    return any(marker in text for marker in markers)


def is_live_question(question):
    q = question.lower()
    phrases = (
        "current", "currently", "today", "tonight", "latest", "live",
        "right now", "this week", "this month", "2026", "2027",
        "president", "prime minister", "chief minister", "chief ministers",
        "gold rate", "gold price", "silver rate", "silver price",
        "stock price", "share price", "exchange rate", "weather", "forecast",
        "news", "election result", "minister", "who won", "score", "match result",
        "petrol price", "diesel price", "fuel price", "market price",
    )
    return any(term in q for term in phrases)


# =========================================================
# TOPIC-AWARE LIVE SEARCH
# =========================================================

def get_search_queries(question):
    q = clean_text(question)
    lower = q.lower()
    today = datetime.now().strftime("%d %B %Y")

    # Chennai / Tamil Nadu gold rates
    if "gold" in lower and any(x in lower for x in ("rate", "price", "cost")):
        city = "Chennai" if "chennai" in lower else "India"
        return [
            f"{city} gold rate today {today} 22 carat 24 carat per gram INR",
            f"{city} 22K gold price today per gram 24K",
            f"{city} gold rate today Goodreturns 22 carat 24 carat",
            f"{city} gold price today Bankbazaar 22k 24k",
            f"{city} gold rate today jewellers 22 carat per gram",
        ]

    # Current Chief Ministers of India
    if "chief minister" in lower or re.search(r"\bcm(s)?\b", lower):
        return [
            f"current chief ministers of all Indian states list {today}",
            f"India chief ministers list states union territories updated {today}",
            "site:india.gov.in chief ministers states India",
            "current chief ministers India state government official websites",
            "Jammu Kashmir Delhi Puducherry chief ministers current",
        ]

    # US President
    if "president" in lower and any(x in lower for x in (
        "united states", "usa", "america", "us president"
    )):
        return [
            "current President of the United States White House",
            "site:whitehouse.gov president Donald Trump",
            "47th President of the United States current official",
        ]

    # Indian Prime Minister / government office holders
    if "prime minister" in lower and ("india" in lower or "indian" in lower):
        return [
            f"current Prime Minister of India official {today}",
            "site:pmindia.gov.in Prime Minister of India",
        ]

    # General current-information queries
    return [
        f"{q} latest information {today}",
        f"{q} official source updated {today}",
        f"{q} latest news today",
    ]


def search_web(question):
    """Search several topic-specific queries and return sourced snippets."""
    results = []
    seen_urls = set()
    seen_text = set()

    try:
        with DDGS() as ddgs:
            for query in get_search_queries(question):
                try:
                    items = ddgs.text(
                        query,
                        region="in-en",
                        safesearch="moderate",
                        max_results=SEARCH_MAX_RESULTS,
                    )

                    for item in items or []:
                        title = clean_text(item.get("title"))
                        body = clean_text(item.get("body"))
                        url = clean_text(item.get("href") or item.get("url"))

                        if not body or not url:
                            continue

                        normalized_url = url.split("#", 1)[0].rstrip("/")
                        normalized_body = re.sub(r"\s+", " ", body).lower()

                        if normalized_url in seen_urls or normalized_body in seen_text:
                            continue

                        seen_urls.add(normalized_url)
                        seen_text.add(normalized_body)
                        results.append(
                            f"Title: {title or 'Search result'}\n"
                            f"Snippet: {body}\n"
                            f"URL: {url}"
                        )

                except Exception as exc:
                    logger.warning("Search query failed: %s | %s", query, exc)

                # Continue through all topic queries to improve coverage.

    except Exception as exc:
        logger.exception("DuckDuckGo search unavailable: %s", exc)

    logger.info("Web search returned %d results for: %s", len(results), question)
    return "\n\n".join(results[:15])


# =========================================================
# FILE READING
# =========================================================

def extract_file_text(uploaded_file):
    filename = secure_filename(uploaded_file.filename or "upload")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        raw = uploaded_file.read()
        uploaded_file.seek(0)

        if extension in ("txt", "csv", "md", "json"):
            return raw.decode("utf-8-sig", errors="replace")

        if extension == "pdf":
            reader = PdfReader(io.BytesIO(raw))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)

        if extension == "docx":
            document = Document(io.BytesIO(raw))
            paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    paragraphs.append(" | ".join(cell.text for cell in row.cells))
            return "\n".join(paragraphs)

        return f"The file '{filename}' was uploaded, but this file type is not supported for text extraction."

    except Exception as exc:
        logger.exception("File extraction failed")
        return f"Could not read uploaded file: {exc}"


# =========================================================
# GEMINI PROVIDER
# =========================================================

def ask_gemini(prompt):
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key is not configured.")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{quote(GEMINI_MODEL, safe='')}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 4096},
    }

    response = requests.post(
        url, params={"key": GEMINI_API_KEY}, json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        raise RuntimeError(f"Gemini API {response.status_code}: {error_text(response)}")

    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")

    parts = candidates[0].get("content", {}).get("parts", [])
    answer = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
    if not answer:
        raise RuntimeError("Gemini returned an empty text response.")
    return answer


# =========================================================
# OPENAI PROVIDER
# =========================================================

def ask_openai(prompt):
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI API key is not configured.")

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": OPENAI_MODEL, "input": prompt, "max_output_tokens": 4096},
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        message = error_text(response)
        if response.status_code == 429 and is_quota_error(message):
            raise RuntimeError("OPENAI_QUOTA_EXHAUSTED: " + message)
        raise RuntimeError(f"OpenAI API {response.status_code}: {message}")

    data = response.json()
    answer = clean_text(data.get("output_text"))

    if not answer:
        chunks = []
        for item in data.get("output", []):
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    chunks.append(part.get("text", ""))
        answer = "\n".join(chunks).strip()

    if not answer:
        raise RuntimeError("OpenAI returned an empty response.")
    return answer


# =========================================================
# GROQ PROVIDER
# =========================================================

def ask_groq(prompt):
    if not GROQ_API_KEY:
        raise RuntimeError("Groq API key is not configured.")

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Uniteaa AI, a helpful and accurate assistant. "
                        "Use supplied search results for current facts. Never invent "
                        "prices, office holders, dates, or citations. State uncertainty."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.5,
            "max_tokens": 4096,
        },
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(f"Groq API {response.status_code}: {error_text(response)}")

    choices = response.json().get("choices", [])
    if not choices:
        raise RuntimeError("Groq returned no choices.")

    answer = clean_text(choices[0].get("message", {}).get("content"))
    if not answer:
        raise RuntimeError("Groq returned an empty response.")
    return answer


# =========================================================
# AUTOMATIC PROVIDER FALLBACK
# =========================================================

def generate_answer(prompt):
    providers = [
        ("Gemini", ask_gemini, bool(GEMINI_API_KEY)),
        ("OpenAI", ask_openai, bool(OPENAI_API_KEY)),
        ("Groq", ask_groq, bool(GROQ_API_KEY)),
    ]
    errors = []

    for name, function, configured in providers:
        if not configured:
            logger.info("%s skipped: API key not configured.", name)
            continue

        try:
            logger.info("Trying AI provider: %s", name)
            answer = function(prompt)
            logger.info("AI response generated by %s", name)
            return answer, name
        except Exception as exc:
            message = str(exc)
            if name == "OpenAI" and is_quota_error(message):
                logger.warning("OpenAI credits/quota unavailable; falling back to Groq.")
            else:
                logger.warning("%s provider failed: %s", name, message)
            errors.append(f"{name}: {message}")

    raise RuntimeError("All configured AI providers failed. " + " | ".join(errors))


# =========================================================
# HOME PAGE
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "assistant": ASSISTANT_NAME,
        "creator": CREATOR,
        "gemini_configured": bool(GEMINI_API_KEY),
        "openai_configured": bool(OPENAI_API_KEY),
        "groq_configured": bool(GROQ_API_KEY),
        "provider_order": ["Gemini", "OpenAI", "Groq"],
        "primary_model": GEMINI_MODEL,
        "openai_model": OPENAI_MODEL,
        "backup_model": GROQ_MODEL,
        "image_model": GEMINI_IMAGE_MODEL,
        "image_generation_configured": bool(GEMINI_API_KEY),
        "live_web_search": True,
        "file_upload": True,
        "file_export": True,
    })


# =========================================================
# CHAT
# =========================================================

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True) or {} if request.is_json else {}
        message = clean_text(
            request.form.get("message") or data.get("message")
        )

        if not message:
            return jsonify({
                "answer": "Please enter a message.",
                "reply": "Please enter a message.",
                "status": "error",
            }), 400

        uploaded_file = request.files.get("file") or request.files.get("files")
        file_text = ""
        if uploaded_file and uploaded_file.filename:
            file_text = extract_file_text(uploaded_file)

        needs_search = is_live_question(message)
        web_context = search_web(message) if needs_search else ""

        if needs_search and not web_context:
            search_note = (
                "Live search was attempted but returned no usable results. "
                "Do not guess or present remembered facts as verified current facts. "
                "Tell the user that current verification was unavailable."
            )
        elif needs_search:
            search_note = (
                "Use the search snippets below. Include the source URLs in your answer. "
                "For gold prices, distinguish 22K from 24K and state the quoted unit "
                "and update date. Do not calculate or invent a price not present in results. "
                "For Chief Ministers, verify the names and identify any missing entries "
                "rather than filling gaps from guesses."
            )
        else:
            search_note = "This is not identified as a live-information question."

        prompt = f"""
You are {ASSISTANT_NAME}, created by {CREATOR}.

Answer the user's actual question in clear, useful language.
Do not fabricate facts, prices, names, dates, citations, or URLs.
When source snippets are supplied, rely on them for current information and cite their URLs.
If sources conflict or are incomplete, explain that briefly.

LIVE SEARCH INSTRUCTION:
{search_note}

USER QUESTION:
{message}

LIVE WEB SEARCH RESULTS:
{web_context if web_context else "No usable live search results were retrieved."}

UPLOADED FILE CONTENT:
{file_text if file_text else "No file was uploaded."}
"""

        answer, provider = generate_answer(prompt)
        return jsonify({
            "answer": answer,
            "reply": answer,
            "provider": provider,
            "live_search_used": bool(web_context),
            "search_attempted": needs_search,
            "status": "success",
        })

    except Exception as exc:
        logger.exception("Chat request failed")
        return jsonify({
            "answer": "I'm temporarily unable to generate an answer. Please try again.",
            "reply": "I'm temporarily unable to generate an answer. Please try again.",
            "error": str(exc),
            "status": "error",
        }), 500


# =========================================================
# GEMINI IMAGE GENERATION
# =========================================================

@app.route("/generate-image", methods=["POST"])
def generate_image():
    try:
        if not GEMINI_API_KEY:
            return jsonify({"success": False, "error": "Gemini API key is not configured."}), 503

        data = request.get_json(silent=True) or {}
        prompt = clean_text(data.get("prompt"))
        if not prompt:
            return jsonify({"success": False, "error": "Please enter an image prompt."}), 400

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(GEMINI_IMAGE_MODEL, safe='')}:generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }

        response = requests.post(
            url, params={"key": GEMINI_API_KEY}, json=payload, timeout=90
        )
        if not response.ok:
            raise RuntimeError(f"Image API {response.status_code}: {error_text(response)}")

        result = response.json()
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline_data = part.get("inlineData") or part.get("inline_data")
                if inline_data and inline_data.get("data"):
                    mime = inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png"
                    image_url = f"data:{mime};base64,{inline_data['data']}"
                    return jsonify({
                        "success": True,
                        "image": image_url,
                        "image_url": image_url,
                    })

        raise RuntimeError("The image model did not return image data.")

    except Exception as exc:
        logger.exception("Image generation failed")
        return jsonify({"success": False, "error": str(exc)}), 500


# =========================================================
# DOCUMENT EXPORT HELPERS
# =========================================================

def make_docx(content):
    document = Document()
    document.add_heading("Uniteaa AI Response", 0)
    for line in content.splitlines():
        document.add_paragraph(line)
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


def make_pdf(content):
    output = io.BytesIO()
    pdf = SimpleDocTemplate(
        output, pagesize=A4,
        rightMargin=45, leftMargin=45,
        topMargin=45, bottomMargin=45,
    )
    styles = getSampleStyleSheet()
    story = [Paragraph("Uniteaa AI Response", styles["Title"]), Spacer(1, 16)]

    for line in content.splitlines():
        safe_line = (
            line.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )
        if safe_line.strip():
            story.append(Paragraph(safe_line, styles["BodyText"]))
            story.append(Spacer(1, 6))

    pdf.build(story)
    output.seek(0)
    return output


@app.route("/export/<file_format>", methods=["POST"])
def export_file(file_format):
    try:
        data = request.get_json(silent=True) or {}
        content = clean_text(data.get("content") or data.get("text") or data.get("answer"))
        if not content:
            return jsonify({"error": "No content to export."}), 400

        fmt = file_format.lower()
        filename = f"uniteaa_ai_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if fmt == "txt":
            output = io.BytesIO(content.encode("utf-8"))
            mimetype = "text/plain"
            filename += ".txt"
        elif fmt == "docx":
            output = make_docx(content)
            mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            filename += ".docx"
        elif fmt == "pdf":
            output = make_pdf(content)
            mimetype = "application/pdf"
            filename += ".pdf"
        else:
            return jsonify({"error": "Supported formats are PDF, DOCX and TXT."}), 400

        return send_file(output, mimetype=mimetype, as_attachment=True, download_name=filename)

    except Exception as exc:
        logger.exception("Export failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/download", methods=["POST"])
def download():
    try:
        data = request.get_json(silent=True) or {}
        content = clean_text(data.get("content") or data.get("text") or data.get("answer"))
        if not content:
            return jsonify({"error": "No content to download."}), 400
        output = io.BytesIO(content.encode("utf-8"))
        return send_file(
            output,
            mimetype="text/plain",
            as_attachment=True,
            download_name="uniteaa_ai_response.txt",
        )
    except Exception as exc:
        logger.exception("Download failed")
        return jsonify({"error": str(exc)}), 500


# =========================================================
# ERROR HANDLERS AND SERVER
# =========================================================

@app.errorhandler(413)
def file_too_large(error):
    return jsonify({"error": "File is too large. Maximum upload size is 20 MB."}), 413


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "The requested page was not found."}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
