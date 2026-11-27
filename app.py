
        answer = generate_answer(message, image_data, web_context)
        if is_live_question(message, live_enabled) and not web_context:
            answer += (
                "\n\nNote: Live web search could not retrieve results just now, "
                "so this answer may not reflect the latest updates."
            )
        return jsonify({
            "answer": answer,
            "live_search_used": live_used
        })

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except requests.RequestException as exc:
        app.logger.exception("Network request failed")
        return jsonify({
            "error": "A network request failed. Please try again shortly."
        }), 502
    except Exception as exc:
        app.logger.exception("Chat request failed")
        return jsonify({"error": str(exc)}), 500

@app.route("/download", methods=["POST"])
def download():
    answer = get_answer_payload()
    if answer is None:
        return jsonify({"error": "Nothing to download."}), 400
    output = io.BytesIO(answer.encode("utf-8"))
    output.seek(0)
    return send_file(
        output, mimetype="text/plain", as_attachment=True,
        download_name="uniteaa_ai_answer.txt"
    )

@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    answer = get_answer_payload()
    if answer is None:
        return jsonify({"error": "Nothing to export."}), 400

    output = io.BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm
    )
    styles = getSampleStyleSheet()
    story = [Paragraph("Uniteaa AI Answer", styles["Title"]), Spacer(1, 8 * mm)]
    for line in answer.splitlines():
        safe_line = html.escape(line) if line else " "
        story.append(Paragraph(safe_line, styles["BodyText"]))
        story.append(Spacer(1, 2 * mm))
    document.build(story)
    output.seek(0)
    return send_file(
        output, mimetype="application/pdf", as_attachment=True,
        download_name="uniteaa_ai_answer.pdf"
    )

@app.route("/export/docx", methods=["POST"])
def export_docx():
    answer = get_answer_payload()
    if answer is None:
        return jsonify({"error": "Nothing to export."}), 400
    document = Document()
    document.add_heading("Uniteaa AI Answer", 0)
    for line in answer.splitlines():
        document.add_paragraph(line)
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype=("application/vnd.openxmlformats-officedocument."
                  "wordprocessingml.document"),
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
        "file_upload": "PDF, DOCX, TXT, CSV, PNG, JPG, WEBP",
        "file_output": "TXT, PDF, DOCX",
        "live_web_search": "enabled (automatic for current/live queries)"
    })

@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\n", mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap():
    host = request.host_url.rstrip("/")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{host}/</loc></url></urlset>"
    )
    return Response(xml, mimetype="application/xml")

@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "File is too large. Maximum size is 10 MB."}), 413

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
