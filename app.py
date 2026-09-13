"""
WaveCode v1 — Flask app
------------------------
Stateless backend around wavecode_core.py:

  - JSON API (for the eventual Next.js frontend):
      POST /api/generate  -> {svg, png_base64, measurements}
      POST /api/decode    -> {name, date, message, additional_info, verified, note}

  - HTML pages (temporary, hand-rolled UI until the API has a real
    frontend): "/" to generate, "/decode" to decode. These call the same
    wavecode_core functions directly — no internal HTTP hop.

Nothing is stored server-side — every request is self-contained.

End users never see how WaveCode is actually encoded/decoded: validation
errors from generate() are about the form fields they typed, so they're
shown as-is, but anything coming out of decode()'s internal photo-reading
process (bar counts, segment boundaries, checksum math, etc.) is caught
here and replaced with one plain-language message — that mechanism is
deliberately not part of the product's surface. `measurements` in the
generate response is the one exception: pure physical geometry (bar
position/width/height, no letters or values), included because a tattoo
artist needs it to draw the design accurately at any scale.
"""

import base64
import io
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file

from wavecode_core import WaveCodeError, decode, generate, png_bytes

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "wavecode-dev")  # only used for flash(); no auth/session data

UNREADABLE_PHOTO_MESSAGE = (
    "We couldn't read a WaveCode from that photo. Make sure it's a clear, "
    "straight-on, well-lit photo of the whole code, uncropped, then try again."
)


def _birth_date_parts(birth_date_str):
    """Parses an ISO 'YYYY-MM-DD' string (what <input type=date> and most
    JSON clients send) into (day, month, year), or (None, None, None)
    if not provided."""
    birth_date_str = (birth_date_str or "").strip()
    if not birth_date_str:
        return None, None, None
    try:
        year_s, month_s, day_s = birth_date_str.split("-")
        return int(day_s), int(month_s), int(year_s)
    except ValueError:
        raise WaveCodeError("Birth date must be in YYYY-MM-DD format.")


def _generate_from_fields(fields):
    """fields: dict with name, birth_date (ISO string, optional), message,
    additional_info. Shared by the HTML route and the JSON API route."""
    day, month, year = _birth_date_parts(fields.get("birth_date"))
    return generate(
        name=fields.get("name", ""),
        day=day, month=month, year=year,
        message=fields.get("message"),
        additional_info=fields.get("additional_info"),
    )


# ----------------------------------------------------------------------
# JSON API
# ----------------------------------------------------------------------

@app.route("/api/generate", methods=["POST"])
def api_generate():
    payload = request.get_json(silent=True) or request.form
    try:
        result = _generate_from_fields(payload)
    except WaveCodeError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "svg": result["svg"],
        "png_base64": base64.b64encode(png_bytes(result["png"])).decode("ascii"),
        "measurements": result["measurements"],
    })


@app.route("/api/decode", methods=["POST"])
def api_decode():
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        return jsonify({"error": "Please provide a photo to decode."}), 400
    try:
        result = decode(photo.stream)
    except Exception:
        return jsonify({"error": UNREADABLE_PHOTO_MESSAGE}), 400

    return jsonify(result)


# ----------------------------------------------------------------------
# HTML pages (temporary UI — will be replaced by a Next.js frontend
# calling the /api/* routes above)
# ----------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def generate_view():
    result = None
    error = None
    form = {"name": "", "birth_date": "", "message": "", "additional_info": ""}

    if request.method == "POST":
        form["name"] = request.form.get("name", "")
        form["birth_date"] = request.form.get("birth_date", "")
        form["message"] = request.form.get("message", "")
        form["additional_info"] = request.form.get("additional_info", "")
        try:
            result = _generate_from_fields(form)
        except WaveCodeError as e:
            error = str(e)

    return render_template("generate.html", result=result, error=error, form=form)


@app.route("/download/<fmt>", methods=["POST"])
def download(fmt):
    try:
        result = _generate_from_fields(request.form)
    except WaveCodeError as e:
        return str(e), 400

    file_stub = f"wavecode_{request.form.get('name', 'code').strip().upper() or 'CODE'}"
    if fmt == "svg":
        buf = io.BytesIO(result["svg"].encode("utf-8"))
        return send_file(buf, mimetype="image/svg+xml", as_attachment=True,
                          download_name=f"{file_stub}.svg")
    elif fmt == "png":
        buf = io.BytesIO(png_bytes(result["png"]))
        return send_file(buf, mimetype="image/png", as_attachment=True,
                          download_name=f"{file_stub}.png")
    else:
        return "Unknown format", 400


@app.route("/decode", methods=["GET", "POST"])
def decode_view():
    result = None
    error = None

    if request.method == "POST":
        photo = request.files.get("photo")
        if not photo or not photo.filename:
            error = "Please choose a photo to decode."
        else:
            try:
                result = decode(photo.stream)
            except Exception:
                error = UNREADABLE_PHOTO_MESSAGE

    return render_template("decode.html", result=result, error=error)


if __name__ == "__main__":
    app.run(debug=True)
