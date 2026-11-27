
import os
import io
import re
import json
import math
import requests

from datetime import datetime
from flask import (
    Flask, render_template, request, jsonify,
    send_file, Response
)
from werkzeug.utils import secure_filename

from pypdf import PdfReader
from docx import Document
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

# ---------------- CONFIGURATION ----------------

ASSISTANT_NAME = "Uniteaa AI"
CREATOR_NAME = "M. Mehanethra"
FATHER_NAME = "K. Manoharan"
MOTHER_NAME = "K. Marakatham"
NATIVE_PLACE = "Thanjavur, Tamil Nadu"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

ALLOWED_EXTENSIONS = {
    "pdf", "docx", "txt", "csv", "png", "jpg", "jpeg", "webp"
}

SYSTEM_PROMPT = f"""
You are {ASSISTANT_NAME}, a helpful AI assistant.

Your creator is {CREATOR_NAME}.
The creator's native place is {NATIVE_PLACE}.
The creator's father's name is {FATHER_NAME}.
The creator's mother's name is {MOTHER_NAME}.

If asked who created you, say you were created by {CREATOR_NAME}.
If asked to tell about your creator, you may share the creator's
name, native place, and parents' names.
If asked where your creator is from, say {NATIVE_PLACE}.
If asked about the creator's parents, provide the names above.

Never reveal the creator's school name or current living location.
Do not guess or invent personal information.

Answer clearly, accurately, and naturally.
If you do not know something, say so.
Do not claim to have searched the live web.
"""

# ---------------- CREATOR QUESTIONS ----------------

def creator_answer(message):
    q = message.lower().strip()

    creator_patterns = [
        "who created you", "who is your creator",
        "who made you", "who developed you",
        "who is the founder", "who is your developer",
        "tell me about your creator", "about your creator",
        "tell me about her", "who is she"
    ]

    parent_patterns = [
        "creator's parents", "your creator parents",
        "who are her parents", "who is her father",
        "who is her mother", "creator father",
        "creator mother", "parents name"
    ]

    father_patterns = ["father name", "her father", "creator's father"]
    mother_patterns = ["mother name", "her mother", "creator's mother"]
    place_patterns = [
        "where is your creator from", "creator native place",
        "where is she from", "her native place"
    ]

    if any(x in q for x in father_patterns):
        return f"My creator's father's name is {FATHER_NAME}."

    if any(x in q for x in mother_patterns):
        return f"My creator's mother's name is {MOTHER_NAME}."

    if any(x in q for x in parent_patterns):
        return (
            f"My creator's father is {FATHER_NAME}, "
            f"and her mother is {MOTHER_NAME}."
        )

    if any(x in q for x in place_patterns):
        return f"My creator's native place is {NATIVE_PLACE}."

    if any(x in q for x in creator_patterns):
        return (
            f"I was created by {CREATOR_NAME}. "
            f"Her native place is {NATIVE_PLACE}. "
            f"Her father's name is {FATHER_NAME}, "
            f"and her mother's name is {MOTHER_NAME}."
        )

    return None


# ---------------- FILE READING ----------------

def allowed_file(filename):
    return (
        "." in filename and
        filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def extract_file_text(file):
    filename = secure_filename(file.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    raw = file.read()
    file.seek(0)

    if extension == "txt":
        return raw.decode("utf-8", errors="replace"), None

    if extension == "csv":
        return raw.decode("utf-8", errors="replace"), None

    if extension == "pdf":
        reader = PdfReader(io.BytesIO(raw))
        text_content = "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
        return text_content, None

    if extension == "docx":
        doc = Document(io.BytesIO(raw))
        text_content = "\n".join(p.text for p in doc.paragraphs)
        return text_content, None

    if extension in {"png", "jpg", "jpeg", "webp"}:
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp"
        }[extension]
        import base64
        return None, {
            "mime_type": mime,
            "data": base64.b64encode(raw).decode("utf-8")
        }

    raise ValueError("Unsupported file format.")


# ---------------- GEMINI AI ----------------

def ask_gemini(message, image_data=None):
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key is not configured.")

    parts = [{"text": SYSTEM_PROMPT + "\n\nUser: " + message}]

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
                "temperature": 0.7,
                "maxOutputTokens": 2048
            }
        },
        timeout=60
    )

    response.raise_for_status()
    data = response.json()

    return data["candidates"][0]["content"]["parts"][0]["text"]


# ---------------- GROQ BACKUP ----------------

def ask_groq(message):
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message}
            ],
            "temperature": 0.7,
            "max_tokens": 2048
        },
        timeout=60
    )

    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


# ---------------- AI RESPONSE ----------------

def generate_answer(message, image_data=None):
    special = creator_answer(message)
    if special:
        return special

    errors = []

    try:
        return ask_gemini(message, image_data)
    except Exception as e:
        errors.append(f"Gemini: {str(e)}")

    # Groq is a text backup. Image analysis requires Gemini.
    if image_data:
        raise RuntimeError(
            "Image analysis is temporarily unavailable. "
            "Please check the Gemini API key. " + " ".join(errors)
        )

    try:
        return ask_groq(message)
    except Exception as e:
        errors.append(f"Groq: {str(e)}")

    raise RuntimeError(
        "Both AI services are unavailable. Please check your API keys. "
        + " | ".join(errors)
    )


# ---------------- DISTANCE CALCULATOR ----------------

def get_coordinates(place):
    url = "https://nominatim.openstreetmap.org/search"
    response = requests.get(
        url,
        params={"q": place, "format": "json", "limit": 1},
        headers={"User-Agent": "UniteaaAI/1.0"},
        timeout=20
    )
    response.raise_for_status()
    results = response.json()

    if not results:
        raise ValueError(f"Could not find location: {place}")

    return float(results[0]["lat"]), float(results[0]["lon"])


def calculate_distance(origin, destination):
    lat1, lon1 = get_coordinates(origin)
    lat2, lon2 = get_coordinates(destination)

    route_url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}"
    )

    response = requests.get(
        route_url,
        params={"overview": "false"},
        timeout=30
    )
    response.raise_for_status()
    data = response.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("Unable to calculate the road distance.")

    route = data["routes"][0]
    km = route["distance"] / 1000
    minutes = route["duration"] / 60

    return (
        f"Driving distance from {origin} to {destination} is "
        f"approximately {km:.1f} km. Estimated driving time: "
        f"{minutes:.0f} minutes. Actual travel time may vary."
    )


def distance_question(message):
    patterns = [
        r"distance between (.+?) and (.+)",
        r"distance from (.+?) to (.+)",
        r"how far is (.+?) from (.+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, message.lower())
        if match:
            if pattern.startswith("how far"):
                destination, origin = match.groups()
            else:
                origin, destination = match.groups()

            return origin.strip(" ?."), destination.strip(" ?.")

    return None


# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    try:
        message = request.form.get("message", "").strip()
        uploaded = request.files.get("file")
        image_data = None

        if uploaded and uploaded.filename:
            if not allowed_file(uploaded.filename):
                return jsonify({
                    "error": "Unsupported file. Upload PDF, DOCX, TXT, CSV, PNG, JPG or WEBP."
                }), 400

            file_text, image_data = extract_file_text(uploaded)

            if file_text is not None:
                if len(file_text) > 60000:
                    file_text = file_text[:60000]
                message = (
                    message or "Please read and explain this file."
                ) + "\n\nUploaded file content:\n" + file_text

            elif image_data:
                message = message or "Please describe and explain this image."

        if not message:
            return jsonify({"error": "Please enter a message or upload a file."}), 400

        distance = distance_question(message)
        if distance and not uploaded:
            try:
                answer = calculate_distance(distance[0], distance[1])
                return jsonify({"answer": answer})
            except Exception:
                pass

        answer = generate_answer(message, image_data)
        return jsonify({"answer": answer})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    answer = data.get("answer", "").strip()

    if not answer:
        return jsonify({"error": "Nothing to download."}), 400

    content = io.BytesIO(answer.encode("utf-8"))
    content.seek(0)

    return send_file(
        content,
        mimetype="text/plain",
        as_attachment=True,
        download_name="uniteaa_ai_answer.txt"
    )


@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    data = request.get_json(silent=True) or {}
    answer = data.get("answer", "").strip()

    if not answer:
        return jsonify({"error": "Nothing to export."}), 400

    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    text_object = pdf.beginText(50, 800)
    text_object.setFont("Helvetica", 10)

    for line in answer.splitlines():
        while len(line) > 95:
            text_object.textLine(line[:95])
            line = line[95:]
        text_object.textLine(line)

        if text_object.getY() < 60:
            pdf.drawText(text_object)
            pdf.showPage()
            text_object = pdf.beginText(50, 800)
            text_object.setFont("Helvetica", 10)

    pdf.drawText(text_object)
    pdf.save()
    output.seek(0)

    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="uniteaa_ai_answer.pdf"
    )


@app.route("/export/docx", methods=["POST"])
def export_docx():
    data = request.get_json(silent=True) or {}
    answer = data.get("answer", "").strip()

    if not answer:
        return jsonify({"error": "Nothing to export."}), 400

    doc = Document()
    doc.add_heading("Uniteaa AI Answer", 0)

    for line in answer.splitlines():
        doc.add_paragraph(line)

    output = io.BytesIO()
    doc.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        as_attachment=True,
        download_name="uniteaa_ai_answer.docx"
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "assistant": ASSISTANT_NAME,
        "creator": CREATOR_NAME,
        "primary_model": GEMINI_MODEL,
        "backup_model": GROQ_MODEL,
        "gemini_configured": bool(GEMINI_API_KEY),
        "groq_configured": bool(GROQ_API_KEY),
        "distance_calculator": "enabled",
        "voice_dictation": "browser-supported",
        "file_upload": "enabled",
        "file_output": "TXT, PDF, DOCX",
        "live_web_search": "disabled"
    })


@app.route("/robots.txt")
def robots():
    return Response(
        "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n",
        mimetype="text/plain"
    )


@app.route("/sitemap.xml")
def sitemap():
    host = request.host_url.rstrip("/")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{host}/</loc></url>
</urlset>"""
    return Response(xml, mimetype="application/xml")


@app.errorhandler(413)
def too_large(error):
    return jsonify({"error": "File is too large. Maximum size is 10 MB."}), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
