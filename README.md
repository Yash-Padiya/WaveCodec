# 〜 WaveCodec

**🔗 Live: [wavecodec.onrender.com](https://wavecodec.onrender.com)**

**Turn a name, a date, and a message into a one-of-a-kind waveform — small enough for a tattoo, a sticker, or a phone case, and readable back from nothing but a photo.**

WaveCodec encodes the people and moments you want to carry with you into a sequence of bars, shaped like a soundwave. Anyone with the app can decode a photo of it back into the name, date, and message — no account, no server-side storage, no app install required for the wearer.

---

## ✨ Features

- **Encode what matters** — a name (required), and optionally a birth date, a short message, and a line of additional info.
- **Two output formats** — download as **SVG** (vector, perfect for a tattoo artist to trace at any size) or **PNG** (raster, ready for a sticker or phone case print).
- **Decode from a photo** — upload a picture of a printed/inked WaveCode and get the encoded fields back.
- **Self-checking** — every code carries a built-in checksum, so a worn tattoo or a slightly rough photo is still readable, and a genuinely unreadable photo fails clearly instead of returning garbage.
- **Stateless** — nothing is stored server-side; every request is self-contained.


## 🖥️ Try it

**Live at [wavecodec.onrender.com](https://wavecodec.onrender.com)** — no install needed:

- **[`/`](https://wavecodec.onrender.com/)** — generate a WaveCode from a name/date/message.
- **[`/decode`](https://wavecodec.onrender.com/decode)** — upload a photo and decode it back.

Or run it locally (see [Getting Started](#-getting-started)) if you want to hack on it.

## 🧰 Tech stack

| Layer | Choice |
|---|---|
| Backend | [Flask](https://flask.palletsprojects.com/) 3 |
| Image generation/reading | [Pillow](https://python-pillow.org/) + [NumPy](https://numpy.org/) |
| Templating | Jinja2 (server-rendered HTML, temporary until a dedicated frontend lands) |
| Production server | [Gunicorn](https://gunicorn.org/) |
| Container | Docker (`python:3.12-slim` base) |

## 🚀 Getting Started

Requirements: **Python 3.12+**

```bash
# 1. Clone and enter the project
git clone 
cd WaveCodec

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate it
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run the app
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.


### Configuration

The app reads its config from environment variables (see `.env`):

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | No | Used only for Flask's `flash()` messaging — no auth/session data depends on it. Defaults to a dev value if unset. |


## 🐳 Running with Docker

The included `Dockerfile` builds a production image serving the app with Gunicorn:

```bash
docker build -t wavecodec .
docker run -p 8000:8000 --env-file .env wavecodec
```

The app will be available at **http://localhost:8000**.

## 📁 Project Structure

```
.
├── app.py               # Flask routes: JSON API + HTML pages
├── wavecode_core.py     # Encoding/decoding engine (single source of truth)
├── templates/           # Jinja2 templates for the HTML pages
├── static/              # Stylesheet
├── requirements.txt     # Python dependencies
├── Dockerfile           # Production image (Gunicorn)
└── .env                 # Local environment variables (not committed)
```

## 🗺️ Roadmap

- Replace the hand-rolled HTML pages with a dedicated frontend, consuming `/api/generate` and `/api/decode` directly.

