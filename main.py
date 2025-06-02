import os
import io
import sys
import time
import json
import zipfile
import logging
import traceback
import functools
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass, field
from flask import Flask, request, abort, send_file, render_template
from werkzeug.utils import secure_filename

import fitz  # PyMuPDF
from PIL import Image, ImageDraw
import openai
from flask import redirect, url_for

app = Flask(__name__) 

@app.route("/upload", methods=["GET"])
def upload_get():
    return redirect(url_for("index"))
# Add at the very top, before importing openai, to load the API key from env if exists:
openai.api_key = os.environ.get("OPENAI_API_KEY") or openai.api_key

if not openai.api_key:
    logger.error("OpenAI API key not set in environment variable OPENAI_API_KEY")
    raise RuntimeError("OpenAI API key is required. Please set the environment variable OPENAI_API_KEY.")

# (You can keep your hardcoded key as fallback or remove it in production)


# -------- Logging Setup --------
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] %(levelname)s:%(name)s:%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# -------- Constants --------
DEFAULT_UPLOAD_DIR = "uploads"
DEFAULT_SCREENSHOT_DIR = "screenshots"
DEFAULT_RESULT_DIR = "results"

MAX_OPENAI_TOKENS = 1000
MAX_AI_RETRIES = 3
AI_RETRY_BASE_DELAY = 3  # seconds

# -------- Data Classes --------

@dataclass
class BoundingBox:
    x0: float = 0
    y0: float = 0
    x1: float = 0
    y1: float = 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Optional['BoundingBox']:
        if not d:
            return None
        try:
            return cls(
                x0=float(d.get("x0", 0)),
                y0=float(d.get("y0", 0)),
                x1=float(d.get("x1", 0)),
                y1=float(d.get("y1", 0)),
            )
        except Exception as e:
            logger.error(f"Invalid bounding box data {d}: {e}")
            return None

    def clamp(self, max_width: float, max_height: float) -> 'BoundingBox':
        return BoundingBox(
            x0=max(0, min(self.x0, max_width)),
            y0=max(0, min(self.y0, max_height)),
            x1=max(0, min(self.x1, max_width)),
            y1=max(0, min(self.y1, max_height)),
        )

    def width(self) -> float:
        return max(0, self.x1 - self.x0)

    def height(self) -> float:
        return max(0, self.y1 - self.y0)


@dataclass
class QuestionBlocks:
    question: Optional[BoundingBox] = None
    choices: Dict[str, Optional[BoundingBox]] = field(default_factory=lambda: {"A": None, "B": None, "C": None, "D": None})
    solution: Optional[BoundingBox] = None


@dataclass
class Metadata:
    level: str = ""
    month: str = ""
    year: str = ""
    exam_type: str = ""

    def sanitized(self) -> 'Metadata':
        # Simple sanitization for filenames
        def clean(s: str) -> str:
            return "".join(c for c in s if c.isalnum() or c in "-_").strip()

        return Metadata(
            level=clean(self.level),
            month=clean(self.month),
            year=clean(self.year),
            exam_type=clean(self.exam_type),
        )

    def base_filename(self) -> str:
        parts = [self.level, self.month, self.year, self.exam_type]
        filtered = [p for p in parts if p]
        return "_".join(filtered) or "exam"


# -------- Utility Functions --------

def create_dir_if_missing(path: str) -> None:
    if not os.path.exists(path):
        try:
            os.makedirs(path, exist_ok=True)
            logger.debug(f"Created directory: {path}")
        except Exception as e:
            logger.error(f"Failed to create directory {path}: {e}")

def save_blank_image(path: str, size: Tuple[int, int] = (800, 600), color=(255, 255, 255)) -> None:
    try:
        img = Image.new("RGB", size, color)
        img.save(path)
        logger.debug(f"Saved blank image at {path}")
    except Exception as e:
        logger.error(f"Failed to save blank image at {path}: {e}")

def load_openai_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        logger.debug("Loaded OpenAI API key from environment.")
        return key
    raise RuntimeError("OpenAI API key is required. Set it as environment variable OPENAI_API_KEY.")

def retry(func, max_attempts=3, base_delay=3, *args, **kwargs):
    attempt = 0
    while attempt < max_attempts:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Attempt {attempt+1} failed with error: {e}")
            time.sleep(base_delay * (2 ** attempt))
            attempt += 1
    raise RuntimeError(f"Function {func.__name__} failed after {max_attempts} attempts.")

def save_ai_response_log(response_text: str, filename: str) -> None:
    logs_dir = "ai_logs"
    create_dir_if_missing(logs_dir)
    path = os.path.join(logs_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(response_text)
        logger.debug(f"Saved AI response log at {path}")
    except Exception as e:
        logger.error(f"Failed to save AI response log at {path}: {e}")


# -------- OpenAI API Interaction --------

def call_openai_chat(
    messages: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    temperature: float = 0.0
) -> str:
    logger.debug(f"Calling OpenAI Chat API with {len(messages)} messages")
    response = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=MAX_OPENAI_TOKENS
    )
    logger.debug("Received OpenAI response")
    return response["choices"][0]["message"]["content"]


# -------- AI Parsing for Bounding Boxes --------

def extract_ai_blocks(text: str, question_number: int) -> Optional[QuestionBlocks]:
    system_prompt = (
        "You are an assistant that extracts bounding box data from a page of an exam PDF.\n"
        "The input text is the full OCR-extracted text of the page.\n"
        "Return a JSON with fields:\n"
        "  question: {x0, y0, x1, y1}\n"
        "  choices: {A: {x0, y0, x1, y1}, B: {...}, C: {...}, D: {...}}\n"
        "  solution: {x0, y0, x1, y1}\n"
        "Coordinates must be floats.\n"
        "If any block is missing, set it to null.\n"
        "Respond only with JSON object."
    )

    user_prompt = f"Page OCR text:\n{text}\n\nExtract bounding boxes for question #{question_number}."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        ai_response = retry(call_openai_chat, max_attempts=MAX_AI_RETRIES, base_delay=AI_RETRY_BASE_DELAY, messages=messages)
        save_ai_response_log(ai_response, f"question_{question_number}.json")
        parsed = json.loads(ai_response)

        question_bbox = BoundingBox.from_dict(parsed.get("question") or {})
        choices_dict_raw = parsed.get("choices") or {}
        choices = {}
        for letter in ['A', 'B', 'C', 'D']:
            bbox_dict = choices_dict_raw.get(letter)
            choices[letter] = BoundingBox.from_dict(bbox_dict or {})

        solution_bbox = BoundingBox.from_dict(parsed.get("solution") or {})

        return QuestionBlocks(question=question_bbox, choices=choices, solution=solution_bbox)

    except Exception as e:
        logger.error(f"AI parsing failed for question {question_number}: {e}")
        return None


# -------- PDF & Image Processing --------

def crop_and_save_image(
    pdf_page: fitz.Page,
    bbox: BoundingBox,
    save_path: str,
    zoom: float = 2.0,
    draw_debug_box: bool = False
) -> None:
    try:
        page_rect = pdf_page.rect
        bbox_clamped = bbox.clamp(page_rect.width, page_rect.height)
        clip_rect = fitz.Rect(bbox_clamped.x0, bbox_clamped.y0, bbox_clamped.x1, bbox_clamped.y1)
        mat = fitz.Matrix(zoom, zoom)
        pix = pdf_page.get_pixmap(matrix=mat, clip=clip_rect, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        if draw_debug_box:
            draw = ImageDraw.Draw(img)
            draw.rectangle([0, 0, pix.width - 1, pix.height - 1], outline="red", width=3)

        img.save(save_path)
        logger.debug(f"Saved cropped image to {save_path}")
    except Exception as e:
        logger.error(f"Error cropping and saving image to {save_path}: {e}")
        save_blank_image(save_path)


def save_images_for_question(
    question_num: int,
    pdf_page: fitz.Page,
    sol_page: Optional[fitz.Page],
    blocks: QuestionBlocks,
    base_name: str,
    screenshots_folder: str
) -> List[str]:
    saved_files = []

    def save_if_bbox(bbox: Optional[BoundingBox], suffix: str):
        if bbox is None or bbox.width() == 0 or bbox.height() == 0:
            logger.warning(f"No valid bounding box for {suffix} on question {question_num}")
            return None
        filename = f"{base_name}_Q{question_num}_{suffix}.png"
        filepath = os.path.join(screenshots_folder, filename)
        crop_and_save_image(pdf_page, bbox, filepath)
        saved_files.append(filepath)
        return filepath

    # Save question block
    save_if_bbox(blocks.question, "question")

    # Save choices A-D individually
    for choice_letter in ['A', 'B', 'C', 'D']:
        save_if_bbox(blocks.choices.get(choice_letter), f"choice_{choice_letter}")

    # Save solution block if solution PDF provided
    if sol_page and blocks.solution:
        filename = f"{base_name}_Q{question_num}_solution.png"
        filepath = os.path.join(screenshots_folder, filename)
        crop_and_save_image(sol_page, blocks.solution, filepath)
        saved_files.append(filepath)
    else:
        logger.info(f"No solution page or solution bounding box for question {question_num}")

    return saved_files


def extract_text_from_page(pdf_page: fitz.Page) -> str:
    try:
        text = pdf_page.get_text("text")
        logger.debug(f"Extracted text from page {pdf_page.number}")
        return text
    except Exception as e:
        logger.error(f"Failed to extract text from page {pdf_page.number}: {e}")
        return ""


# -------- Main PDF Processing Pipeline --------

def process_pdfs(
    test_pdf_path: str,
    sol_pdf_path: Optional[str],
    metadata: Metadata
) -> str:
    metadata = metadata.sanitized()
    base_name = metadata.base_filename()
    screenshots_folder = os.path.join(DEFAULT_SCREENSHOT_DIR, base_name)
    create_dir_if_missing(screenshots_folder)

    try:
        test_doc = fitz.open(test_pdf_path)
    except Exception as e:
        logger.error(f"Failed to open test PDF {test_pdf_path}: {e}")
        raise RuntimeError(f"Failed to open test PDF: {e}")

    sol_doc = None
    if sol_pdf_path:
        try:
            sol_doc = fitz.open(sol_pdf_path)
        except Exception as e:
            logger.warning(f"Could not open solution PDF {sol_pdf_path}: {e}")
            sol_doc = None

    all_saved_files = []
    skipped_questions = 0

    logger.info(f"Starting PDF processing for {test_pdf_path} with {len(test_doc)} pages")

    question_number = 1
    for page_num in range(len(test_doc)):
        pdf_page = test_doc[page_num]
        text = extract_text_from_page(pdf_page)

        if not text.strip():
            logger.warning(f"No text extracted from page {page_num + 1}")
            continue

        # Use AI to extract bounding boxes for this question/page
        blocks = extract_ai_blocks(text, question_number)
        if blocks is None:
            logger.warning(f"Skipping question {question_number} due to AI parse failure")
            skipped_questions += 1
            question_number += 1
            continue

        sol_page = sol_doc[page_num] if sol_doc and page_num < len(sol_doc) else None

        saved = save_images_for_question(
            question_number, pdf_page, sol_page, blocks, base_name, screenshots_folder
        )
        all_saved_files.extend(saved)
        question_number += 1

    # Zip all saved screenshots into a single archive for user
    zip_filename = f"{base_name}_screenshots.zip"
    zip_path = os.path.join(DEFAULT_RESULT_DIR, zip_filename)
    create_dir_if_missing(DEFAULT_RESULT_DIR)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in all_saved_files:
                arcname = os.path.relpath(file_path, screenshots_folder)
                zf.write(file_path, arcname)
        logger.info(f"Created ZIP archive at {zip_path} containing {len(all_saved_files)} images.")
    except Exception as e:
        logger.error(f"Failed to create ZIP archive: {e}")
        raise RuntimeError("Failed to create ZIP archive.")

    logger.info(f"Processing complete. Saved {len(all_saved_files)} images. Skipped {skipped_questions} questions.")
    return zip_path


# -------- Flask Web Server Setup --------

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # Max 50MB upload


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # process the uploaded files and form data here
        ...
        return send_file("output.zip", as_attachment=True)
    return render_template("index.html")



def allowed_file(filename: str) -> bool:
    return filename.lower().endswith(".pdf")


@app.route("/upload", methods=["POST"])
def upload():
    if 'test_pdf' not in request.files or 'sol_pdf' not in request.files:
        logger.warning("Missing PDF files in upload request.")
        abort(400, "Missing PDF files.")

    test_pdf_file = request.files['test_pdf']
    sol_pdf_file = request.files['sol_pdf']

    # Get metadata fields from form
    level = request.form.get("level", "").strip()
    month = request.form.get("month", "").strip()
    year = request.form.get("year", "").strip()
    exam_type = request.form.get("type", "").strip()

    metadata = Metadata(level=level, month=month, year=year, exam_type=exam_type)

    # Validate file extensions
    if not (test_pdf_file and allowed_file(test_pdf_file.filename)):
        logger.warning("Invalid test PDF upload.")
        abort(400, "Invalid test PDF file.")
    if not (sol_pdf_file and allowed_file(sol_pdf_file.filename)):
        logger.warning("Invalid solution PDF upload.")
        abort(400, "Invalid solution PDF file.")

    create_dir_if_missing(DEFAULT_UPLOAD_DIR)
    test_pdf_path = os.path.join(DEFAULT_UPLOAD_DIR, secure_filename(test_pdf_file.filename))
    sol_pdf_path = os.path.join(DEFAULT_UPLOAD_DIR, secure_filename(sol_pdf_file.filename))

    test_pdf_file.save(test_pdf_path)
    sol_pdf_file.save(sol_pdf_path)

    try:
        zip_path = process_pdfs(test_pdf_path, sol_pdf_path, metadata)
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        abort(500, f"Processing error: {e}")

    return send_file(zip_path, as_attachment=True)


@app.route("/status", methods=["GET"])
def status():
    return {"status": "running", "uptime": time.time()}


# -------- Main Entrypoint --------

if __name__ == "__main__":
    import os
    # Insert your OpenAI key here directly as per your request:
    openai.api_key = "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"

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

