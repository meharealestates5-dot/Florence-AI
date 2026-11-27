
import os
import io
import re
import csv
import json
import base64
import logging
import tempfile
import requests

from datetime import datetime
from urllib.parse import quote
from flask import (
    Flask, render_template, request, jsonify,
    send_file, make_response
)
from werkzeug.utils import secure_filename

from pypdf import PdfReader
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from ddgs import DDGS


# =========================================================
# APP CONFIGURATION
# =========================================================

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "uniteaa-ai-secret-key")

app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uniteaa")

ASSISTANT_NAME = "Uniteaa AI"
CREATOR = "M. Mehanethra"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL", "gemini-2.5-flash"
)
GEMINI_IMAGE_MODEL = os.getenv(
    "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"
)
OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL", "gpt-5.4-mini"
)
GROQ_MODEL = os.getenv(
    "GROQ_MODEL", "openai/gpt-oss-120b"
)

REQUEST_TIMEOUT = 45

conversation_exports = {}


# =========================================================
# GENERAL HELPERS
# =========================================================

def error_text(response):
    try:
        data = response.json()
        return str(data.get("error", {}).get("message", data))
    except Exception:
        return response.text[:1000]


def is_quota_error(message):
    message = str(message).lower()

    indicators = [
        "insufficient_quota",
        "credit_balance_exhausted",
        "no credits remaining",
        "exceeded your current quota",
        "billing hard limit",
    ]

    return any(item in message for item in indicators)


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


# =========================================================
# LIVE WEB SEARCH
# =========================================================

def get_search_queries(question):
    q = question.strip()

    return [
        q,
        q + " latest official information",
        q + " current update India"
    ]


def search_web(question):
    results = []
    seen = set()

    try:
        with DDGS() as ddgs:
            for query in get_search_queries(question):
                try:
                    items = ddgs.text(
                        query,
                        max_results=4
                    )

                    for item in items or []:
                        title = item.get("title", "")
                        body = item.get("body", "")
                        url = item.get("href", "")

                        if not body or url in seen:
                            continue

                        seen.add(url)

                        results.append(
                            f"Title: {title}\n"
                            f"Information: {body}\n"
                            f"Source: {url}"
                        )

                    if len(results) >= 8:
                        break

                except Exception as exc:
                    logger.warning(
                        "Search query failed: %s | %s",
                        query, exc
                    )

    except Exception as exc:
        logger.warning("Web search unavailable: %s", exc)

    return "\n\n".join(results[:8])


# =========================================================
# FILE READING
# =========================================================

def extract_file_text(file):
    filename = secure_filename(file.filename or "upload")
    extension = filename.rsplit(".", 1)[-1].lower()

    try:
        data = file.read()
        file.seek(0)

        if extension == "txt":
            return data.decode("utf-8", errors="ignore")

        if extension == "csv":
            return data.decode("utf-8", errors="ignore")

        if extension == "pdf":
            reader = PdfReader(io.BytesIO(data))
            pages = []

            for page in reader.pages:
                pages.append(page.extract_text() or "")

            return "\n".join(pages)

        if extension == "docx":
            document = Document(io.BytesIO(data))
            return "\n".join(
                paragraph.text
                for paragraph in document.paragraphs
            )

        return (
            f"Uploaded file: {filename}. "
            "Text extraction is not supported for this file type."
        )

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
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{quote(GEMINI_MODEL, safe='')}:generateContent"
    )

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096
        }
    }

    response = requests.post(
        url,
        params={"key": GEMINI_API_KEY},
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:
        raise RuntimeError(
            f"Gemini API {response.status_code}: "
            f"{error_text(response)}"
        )

    data = response.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned an empty response.")

    parts = candidates[0].get("content", {}).get("parts", [])

    answer = "\n".join(
        part.get("text", "")
        for part in parts
        if part.get("text")
    ).strip()

    if not answer:
        raise RuntimeError("Gemini returned no text.")

    return answer


# =========================================================
# OPENAI PROVIDER
# =========================================================

def ask_openai(prompt):
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI API key is not configured.")

    url = "https://api.openai.com/v1/responses"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": OPENAI_MODEL,
        "input": prompt,
        "max_output_tokens": 4096
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:
        message = error_text(response)

        if response.status_code == 429 and is_quota_error(message):
            raise RuntimeError(
                "OPENAI_QUOTA_EXHAUSTED: " + message
            )

        raise RuntimeError(
            f"OpenAI API {response.status_code}: {message}"
        )

    data = response.json()

    answer = data.get("output_text", "")

    if not answer:
        collected = []

        for item in data.get("output", []):
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    collected.append(part.get("text", ""))

        answer = "\n".join(collected)

    if not answer.strip():
        raise RuntimeError("OpenAI returned an empty response.")

    return answer.strip()


# =========================================================
# GROQ PROVIDER
# =========================================================

def ask_groq(prompt):
    if not GROQ_API_KEY:
        raise RuntimeError("Groq API key is not configured.")

    url = "https://api.groq.com/openai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Uniteaa AI, a helpful and accurate "
                    "AI assistant. Answer clearly. Do not invent "
                    "current facts or claim that you verified "
                    "information when you did not."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.7,
        "max_tokens": 4096
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:
        raise RuntimeError(
            f"Groq API {response.status_code}: "
            f"{error_text(response)}"
        )

    data = response.json()

    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("Groq returned an empty response.")

    answer = (
        choices[0]
        .get("message", {})
        .get("content", "")
    )

    if not answer:
        raise RuntimeError("Groq returned no text.")

    return answer.strip()


# =========================================================
# AUTOMATIC PROVIDER FALLBACK
# =========================================================

def generate_answer(prompt):
    providers = [
        ("Gemini", ask_gemini, bool(GEMINI_API_KEY)),
        ("OpenAI", ask_openai, bool(OPENAI_API_KEY)),
        ("Groq", ask_groq, bool(GROQ_API_KEY))
    ]

    errors = []

    for provider_name, provider_function, configured in providers:

        if not configured:
            logger.info(
                "%s skipped: API key not configured.",
                provider_name
            )
            continue

        try:
            logger.info("Trying provider: %s", provider_name)

            answer = provider_function(prompt)

            logger.info(
                "Response successfully generated by %s",
                provider_name
            )

            return answer, provider_name

        except Exception as exc:
            error_message = str(exc)

            if provider_name == "OpenAI" and is_quota_error(error_message):
                logger.warning(
                    "OpenAI credits exhausted. "
                    "Skipping OpenAI and continuing to Groq."
                )
            else:
                logger.warning(
                    "%s provider failed: %s",
                    provider_name,
                    error_message
                )

            errors.append(
                f"{provider_name}: {error_message}"
            )

    raise RuntimeError(
        "All configured AI providers failed. "
        + " | ".join(errors)
    )


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

        "provider_order": [
            "Gemini",
            "OpenAI",
            "Groq"
        ],

        "primary_model": GEMINI_MODEL,
        "openai_model": OPENAI_MODEL,
        "backup_model": GROQ_MODEL,

        "image_model": GEMINI_IMAGE_MODEL,
        "image_generation_configured": bool(GEMINI_API_KEY),

        "live_web_search": True,
        "file_upload": True,
        "file_export": True
    })


# =========================================================
# CHAT
# =========================================================

@app.route("/chat", methods=["POST"])
def chat():
    try:
        message = clean_text(request.form.get("message"))

        if not message and request.is_json:
            data = request.get_json(silent=True) or {}
            message = clean_text(data.get("message"))

        if not message:
            return jsonify({
                "answer": "Please enter a message.",
                "reply": "Please enter a message."
            }), 400

        file_text = ""

        uploaded_file = (
            request.files.get("file")
            or request.files.get("files")
        )

        if uploaded_file and uploaded_file.filename:
            file_text = extract_file_text(uploaded_file)

        web_context = ""

        live_keywords = [
            "current", "today", "latest", "now",
            "president", "chief minister", "gold rate",
            "weather", "news", "price", "2026",
            "election", "minister", "stock"
        ]

        needs_search = any(
            word in message.lower()
            for word in live_keywords
        )

        if needs_search:
            web_context = search_web(message)

        prompt = f"""
You are {ASSISTANT_NAME}, created by {CREATOR}.

IMPORTANT INSTRUCTIONS:
- Answer the user's actual question.
- Use simple, clear language.
- For current information, use the supplied live web results.
- If web search returned no results, do not pretend that
  current information has been verified.
- Do not invent names, dates, prices, facts, or sources.
- Explain uncertainty when reliable information is unavailable.
- Use the uploaded file content when relevant.

USER QUESTION:
{message}

LIVE WEB SEARCH RESULTS:
{web_context if web_context else "No live search results were retrieved."}

UPLOADED FILE CONTENT:
{file_text if file_text else "No file uploaded."}
"""

        answer, provider = generate_answer(prompt)

        return jsonify({
            "answer": answer,
            "reply": answer,
            "provider": provider,
            "live_search_used": bool(web_context),
            "status": "success"
        })

    except Exception as exc:
        logger.exception("Chat request failed")

        message = (
            "I'm temporarily unable to generate an answer. "
            "Please try again in a moment."
        )

        return jsonify({
            "answer": message,
            "reply": message,
            "error": str(exc),
            "status": "error"
        }), 500


# =========================================================
# GEMINI IMAGE GENERATION
# =========================================================

@app.route("/generate-image", methods=["POST"])
def generate_image():
    try:
        if not GEMINI_API_KEY:
            return jsonify({
                "error": "Gemini API key is not configured."
            }), 503

        data = request.get_json(silent=True) or {}
        prompt = clean_text(data.get("prompt"))

        if not prompt:
            return jsonify({
                "error": "Please enter an image prompt."
            }), 400

        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{quote(GEMINI_IMAGE_MODEL, safe='')}:generateContent"
        )

        payload = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"]
            }
        }

        response = requests.post(
            url,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=90
        )

        if not response.ok:
            raise RuntimeError(
                f"Image API {response.status_code}: "
                f"{error_text(response)}"
            )

        result = response.json()

        for candidate in result.get("candidates", []):
            parts = candidate.get("content", {}).get("parts", [])

            for part in parts:
                inline_data = part.get("inlineData")

                if inline_data and inline_data.get("data"):
                    mime = inline_data.get(
                        "mimeType", "image/png"
                    )

                    image_data = inline_data["data"]

                    image_url = (
                        f"data:{mime};base64,{image_data}"
                    )

                    return jsonify({
                        "success": True,
                        "image": image_url,
                        "image_url": image_url
                    })

        raise RuntimeError(
            "The image model did not return an image."
        )

    except Exception as exc:
        logger.exception("Image generation failed")

        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


# =========================================================
# DOCUMENT EXPORT
# =========================================================

def make_txt(content):
    return content.encode("utf-8")


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

    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=45,
        leftMargin=45,
        topMargin=45,
        bottomMargin=45
    )

    styles = getSampleStyleSheet()
    story = []

    story.append(
        Paragraph("Uniteaa AI Response", styles["Title"])
    )
    story.append(Spacer(1, 16))

    for line in content.splitlines():
        safe_line = (
            line.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )

        if safe_line.strip():
            story.append(
                Paragraph(safe_line, styles["BodyText"])
            )
            story.append(Spacer(1, 6))

    document.build(story)
    output.seek(0)

    return output


@app.route("/export/<file_format>", methods=["POST"])
def export_file(file_format):
    try:
        data = request.get_json(silent=True) or {}

        content = clean_text(
            data.get("content")
            or data.get("text")
            or data.get("answer")
        )

        if not content:
            return jsonify({
                "error": "No content to export."
            }), 400

        file_format = file_format.lower()

        if file_format == "txt":
            output = io.BytesIO(make_txt(content))
            mimetype = "text/plain"

        elif file_format == "docx":
            output = make_docx(content)
            mimetype = (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            )

        elif file_format == "pdf":
            output = make_pdf(content)
            mimetype = "application/pdf"

        else:
            return jsonify({
                "error": "Supported formats: PDF, DOCX, TXT."
            }), 400

        filename = f"uniteaa_ai_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{file_format}"

        return send_file(
            output,
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename
        )

    except Exception as exc:
        logger.exception("Export failed")

        return jsonify({
            "error": str(exc)
        }), 500


# =========================================================
# DOWNLOAD ENDPOINT
# =========================================================

@app.route("/download", methods=["POST"])
def download():
    try:
        data = request.get_json(silent=True) or {}

        content = clean_text(
            data.get("content")
            or data.get("text")
            or data.get("answer")
        )

        if not content:
            return jsonify({
                "error": "No content to download."
            }), 400

        output = io.BytesIO(content.encode("utf-8"))

        return send_file(
            output,
            mimetype="text/plain",
            as_attachment=True,
            download_name="uniteaa_ai_response.txt"
        )

    except Exception as exc:
        logger.exception("Download failed")

        return jsonify({
            "error": str(exc)
        }), 500


# =========================================================
# ERROR HANDLERS
# =========================================================

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
    return jsonify({
        "error": "Internal server error."
    }), 500


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
