
from flask import Flask, request, send_file, render_template
import os
import pdfplumber
from PyPDF2 import PdfReader
from PIL import Image
import zipfile
from werkzeug.utils import secure_filename
import re
import fitz  # PyMuPDF for image handling


app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "output"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

def extract_solution_regions(pdf_path):
    """
    Extract solution bounding boxes per question from solution PDF.
    Returns list of dicts with question number, page number, and solution bbox.
    """
    solutions = []
    solution_pattern = r'^\s*(\d+)[\.\)]\s*'

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            words = page.extract_words()
            lines_dict = {}
            for w in words:
                top = round(w['top'])
                if top not in lines_dict:
                    lines_dict[top] = []
                lines_dict[top].append(w)

            sorted_tops = sorted(lines_dict.keys())

            current_solution_num = None
            content_lines = []
            bbox_x0 = bbox_top = bbox_x1 = bbox_bottom = None

            for top in sorted_tops:
                bbox_x0 = float('inf')    # for min calculations
                bbox_top = float('inf')
                bbox_x1 = float('-inf')   # for max calculations
                bbox_bottom = float('-inf')

                line_words = lines_dict[top]
                line_text = " ".join(w['text'] for w in line_words).strip()

                sol_match = re.match(solution_pattern, line_text)
                if sol_match:
                    # Save previous solution
                    if current_solution_num is not None:
                        solutions.append({
                            'number': current_solution_num,
                            'page': page_num,
                            'solution_bbox': (bbox_x0, bbox_top, bbox_x1, bbox_bottom),
                            'content_lines': content_lines
                        })

                    current_solution_num = int(sol_match.group(1))
                    content_lines = [line_text]

                    bbox_x0 = min(w['x0'] for w in line_words)
                    bbox_top = min(w['top'] for w in line_words)
                    bbox_x1 = max(w['x1'] for w in line_words)
                    bbox_bottom = max(w['bottom'] for w in line_words)

                elif current_solution_num is not None:
                    content_lines.append(line_text)
                    bbox_x0 = min(bbox_x0, min(w['x0'] for w in line_words))
                    bbox_top = min(bbox_top, min(w['top'] for w in line_words))
                    bbox_x1 = max(bbox_x1, max(w['x1'] for w in line_words))
                    bbox_bottom = max(bbox_bottom, max(w['bottom'] for w in line_words))

            # Save last solution on page
            if current_solution_num is not None:
                solutions.append({
                    'number': current_solution_num,
                    'page': page_num,
                    'solution_bbox': (bbox_x0, bbox_top, bbox_x1, bbox_bottom),
                    'content_lines': content_lines
                })

    return solutions


def fitz_rect_from_bbox(bbox, zoom=2):
    """
    Convert pdfplumber bbox to fitz.Rect scaled by zoom factor.
    bbox = (x0, top, x1, bottom)
    """
    x0, y0, x1, y1 = bbox
    return fitz.Rect(x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom)

def create_cropped_screenshot(pdf_path, page_num, bbox, output_path, zoom=2):
    """Create cropped screenshot of given bbox in PDF page with zoom, safely."""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    mat = fitz.Matrix(zoom, zoom)

    # Get full image of the page
    pix_full = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix_full.width, pix_full.height], pix_full.samples)

    # Convert bbox and clamp to image size
    rect = fitz_rect_from_bbox(bbox, zoom)
    x0 = max(0, min(int(rect.x0), img.width))
    y0 = max(0, min(int(rect.y0), img.height))
    x1 = max(0, min(int(rect.x1), img.width))
    y1 = max(0, min(int(rect.y1), img.height))

    # Only crop if bbox is valid
    if x1 > x0 and y1 > y0:
        cropped = img.crop((x0, y0, x1, y1))
        cropped.save(output_path)
    else:
        print(f"[Warning] Invalid crop box: page={page_num+1}, bbox={bbox}")
        create_blank_image(output_path)

    doc.close()


def create_blank_image(output_path, width=800, height=600):
    """Create a blank white image"""
    img = Image.new('RGB', (width, height), 'white')
    img.save(output_path)

def find_solution_for_question(sol_pdf_path, question_num):
    """Find the solution page number for a given question number."""
    with pdfplumber.open(sol_pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue

            pattern = rf'\b{question_num}[\.\)]\s*'
            if re.search(pattern, text):
                return page_num
    return None

def create_solution_screenshot(sol_pdf_path, page_num, output_path, zoom=2):
    """Create full page screenshot for solution page."""
    doc = fitz.open(sol_pdf_path)
    page = doc[page_num]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img.save(output_path)
    doc.close()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        test_pdf = request.files["test_pdf"]
        sol_pdf = request.files["sol_pdf"]
        test_path = os.path.join(UPLOAD_FOLDER, secure_filename(test_pdf.filename))
        sol_path = os.path.join(UPLOAD_FOLDER, secure_filename(sol_pdf.filename))
        test_pdf.save(test_path)
        sol_pdf.save(sol_path)

        # Extract combined text for metadata extraction
        with pdfplumber.open(test_path) as pdf:
            test_text = ""
            for page in pdf.pages[:3]:
                pt = page.extract_text()
                if pt:
                    test_text += pt + " "
        with pdfplumber.open(sol_path) as pdf:
            sol_text = ""
            for page in pdf.pages[:3]:
                pt = page.extract_text()
                if pt:
                    sol_text += pt + " "
        combined_text = test_text + " " + sol_text

        # Extract year
        words = combined_text.split()
        year_candidates = [word for word in words if word.isdigit() and len(word) == 4 and word.startswith('20')]
        if not year_candidates:
            year_candidates = [word for word in words if word.isdigit() and len(word) == 4]
        year = year_candidates[0] if year_candidates else "UnknownYear"

        # Extract month
        months = {"January": "Jan", "February": "Feb", "March": "Mar", "April": "Apr",
                  "May": "May", "June": "Jun", "July": "Jul", "August": "Aug",
                  "September": "Sep", "October": "Oct", "November": "Nov", "December": "Dec"}
        month = "UnknownMonth"
        for full_month, short_month in months.items():
            if full_month in combined_text or short_month in combined_text:
                month = short_month
                break

        # Extract type
        type_ = "Reg" if "Regional" in combined_text else "Inv" if "Invitational" in combined_text else "Inv"

        # Extract level
        level = "UnknownLevel"
        for lvl in ["Algebra 1", "Geometry", "Algebra 2", "Precalculus", "Calculus", "Statistics"]:
            if lvl in combined_text:
                level = lvl.replace(" ", "")
                break

        # Extract individual vs team
        indiv = "Indiv" if "Individual" in combined_text else "Team" if "Team" in combined_text else "Unknown"

        # Use user input
        year = request.form.get("year", "UnknownYear")
        month = request.form.get("month", "UnknownMonth")
        type_ = request.form.get("type", "Inv")
        level = request.form.get("level", "UnknownLevel").replace(" ", "")
        indiv = request.form.get("indiv", "Indiv")
        base_name = f"{year}_{month}_{type_}_{level}_{indiv}"
        output_dir = os.path.join(OUTPUT_FOLDER, base_name)
        os.makedirs(output_dir, exist_ok=True)

        new_test_path = os.path.join(output_dir, os.path.basename(test_path))
        new_sol_path = os.path.join(output_dir, os.path.basename(sol_path))
        os.rename(test_path, new_test_path)
        os.rename(sol_path, new_sol_path)

        # Extract questions and choices with bounding boxes
        questions = extract_question_regions(new_test_path)

        for question in questions:
            q_num = question['number']

            # base number for filenames
            if q_num < 10:
                base_num = q_num * 10
            else:
                base_num = q_num * 100

            # 1. Question screenshot (cropped to question bbox)
            question_filename = f"{base_num:06d}_{base_name}.png"
            create_cropped_screenshot(new_test_path, question['page'], question['question_bbox'],
                                     os.path.join(output_dir, question_filename))


            # 2. Answer choice screenshots (A-D), cropped to choice bbox or blank if missing
            choice_letters = ['A', 'B', 'C', 'D']
            for i, letter in enumerate(choice_letters):
                index_number = base_num + i + 1
                choice_filename = f"{index_number:06d}_{base_name}.png"
                choice_obj = next((c for c in question['choices'] if c['letter'] == letter), None)
                if choice_obj:
                    create_cropped_screenshot(new_test_path, question['page'], choice_obj['bbox'],
                                              os.path.join(output_dir, choice_filename))
                else:
                    create_blank_image(os.path.join(output_dir, choice_filename))


            # 3. Solution screenshot (full page)
            # Find matching solution for this question
            sol = next((s for s in solutions if s['number'] == q_num), None)
            solution_index = base_num + 5
            solution_filename = f"{solution_index:06d}_{base_name}.png"
            solution_path = os.path.join(output_dir, solution_filename)

            if sol:
                create_cropped_screenshot(new_sol_path, sol['page'], sol['solution_bbox'], solution_path)
            else:
                create_blank_image(solution_path)


            # 4. Four blank screenshots
            
            for i in range(4):
                blank_index = base_num + 6 + i
                blank_filename = f"{blank_index:06d}_{base_name}.png"
                create_blank_image(os.path.join(output_dir, blank_filename))


        zip_path = os.path.join(OUTPUT_FOLDER, base_name + ".zip")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for file in os.listdir(output_dir):
                if file.endswith('.png'):
                    zipf.write(os.path.join(output_dir, file), arcname=file)

        return send_file(zip_path, as_attachment=True)

    return render_template("index.html")

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 3000))
    print(f"Starting app on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)


