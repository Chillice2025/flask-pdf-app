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

def extract_question_regions(pdf_path):
    questions = []
    question_pattern = r'^\s*(\d+)[\.\)]\s*'
    choice_letters = ['A', 'B', 'C', 'D', 'E']

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
            current_question_num = None
            bbox_x0 = bbox_top = bbox_x1 = bbox_bottom = None
            choices = []
            current_choice_letter = None
            current_choice_bbox = None

            def save_question():
                if current_question_num is not None and bbox_x0 is not None:
                    questions.append({
                        'number': current_question_num,
                        'page': page_num,
                        'question_bbox': (bbox_x0, bbox_top, bbox_x1, bbox_bottom),
                        'choices': choices
                    })

            for top in sorted_tops:
                line_words = lines_dict[top]
                line_text = " ".join(w['text'] for w in line_words).strip()

                q_match = re.match(question_pattern, line_text)
                c_match = None
                for letter in choice_letters:
                    pattern = rf'^{letter}[\.\)]\s*'
                    if re.match(pattern, line_text):
                        c_match = letter
                        break

                if q_match:
                    save_question()
                    current_question_num = int(q_match.group(1))
                    bbox_x0 = min(w['x0'] for w in line_words)
                    bbox_top = min(w['top'] for w in line_words)
                    bbox_x1 = max(w['x1'] for w in line_words)
                    bbox_bottom = max(w['bottom'] for w in line_words)
                    choices = []
                    current_choice_letter = None
                    current_choice_bbox = None

                elif c_match:
                    if current_choice_letter is not None and current_choice_bbox is not None:
                        choices.append({
                            'letter': current_choice_letter,
                            'bbox': current_choice_bbox
                        })
                    current_choice_letter = c_match
                    current_choice_bbox = (
                        min(w['x0'] for w in line_words),
                        min(w['top'] for w in line_words),
                        max(w['x1'] for w in line_words),
                        max(w['bottom'] for w in line_words)
                    )

                elif current_question_num is not None:
                    bbox_x0 = min(bbox_x0, min(w['x0'] for w in line_words))
                    bbox_top = min(bbox_top, min(w['top'] for w in line_words))
                    bbox_x1 = max(bbox_x1, max(w['x1'] for w in line_words))
                    bbox_bottom = max(bbox_bottom, max(w['bottom'] for w in line_words))

                    if current_choice_letter is not None and current_choice_bbox is not None:
                        current_choice_bbox = (
                            min(current_choice_bbox[0], min(w['x0'] for w in line_words)),
                            min(current_choice_bbox[1], min(w['top'] for w in line_words)),
                            max(current_choice_bbox[2], max(w['x1'] for w in line_words)),
                            max(current_choice_bbox[3], max(w['bottom'] for w in line_words)),
                        )

            if current_choice_letter is not None and current_choice_bbox is not None:
                choices.append({
                    'letter': current_choice_letter,
                    'bbox': current_choice_bbox
                })
            save_question()

    for q in questions:
        q['choices'] = [c for c in q['choices'] if c['letter'] in ['A', 'B', 'C', 'D']]

    return questions

def extract_solution_regions(pdf_path):
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

                    bbox_x0 = min([w.get('x0') for w in line_words if w.get('x0') is not None], default=0)
                    bbox_top = min([w.get('top') for w in line_words if w.get('top') is not None], default=0)
                    bbox_x1 = max([w.get('x1') for w in line_words if w.get('x1') is not None], default=0)
                    bbox_bottom = max([w.get('bottom') for w in line_words if w.get('bottom') is not None], default=0)

                elif current_solution_num is not None:
                    content_lines.append(line_text)
                    bbox_x0 = min(bbox_x0, min([w.get('x0') for w in line_words if w.get('x0') is not None], default=0))
                    bbox_top = min(bbox_top, min([w.get('top') for w in line_words if w.get('top') is not None], default=0))
                    bbox_x1 = max(bbox_x1, max([w.get('x1') for w in line_words if w.get('x1') is not None], default=0))
                    bbox_bottom = max(bbox_bottom, max([w.get('bottom') for w in line_words if w.get('bottom') is not None], default=0))

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
    x0, y0, x1, y1 = bbox
    return fitz.Rect(x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom)

def create_cropped_screenshot(pdf_path, page_num, bbox, output_path, zoom=2):
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    mat = fitz.Matrix(zoom, zoom)
    pix_full = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix_full.width, pix_full.height], pix_full.samples)
    rect = fitz_rect_from_bbox(bbox, zoom)
    x0 = max(0, min(int(rect.x0), img.width))
    y0 = max(0, min(int(rect.y0), img.height))
    x1 = max(0, min(int(rect.x1), img.width))
    y1 = max(0, min(int(rect.y1), img.height))
    if x1 > x0 and y1 > y0:
        cropped = img.crop((x0, y0, x1, y1))
        cropped.save(output_path)
    else:
        create_blank_image(output_path)
    doc.close()

def create_blank_image(output_path, width=800, height=600):
    img = Image.new('RGB', (width, height), 'white')
    img.save(output_path)

def find_solution_for_question(sol_pdf_path, question_num):
    with pdfplumber.open(sol_pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
            pattern = rf'\b{question_num}[\.\)]\s*'
            if re.search(pattern, text):
                return page_num
    return -1

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        test_pdf = request.files['test_pdf']
        solution_pdf = request.files['solution_pdf']
        level = request.form['level']
        month = request.form['month']
        year = request.form['year']
        paper_type = request.form['type']

        test_path = os.path.join(UPLOAD_FOLDER, secure_filename(test_pdf.filename or "test.pdf"))
        sol_path = os.path.join(UPLOAD_FOLDER, secure_filename(sol_pdf.filename or "solution.pdf"))


        new_test_path = os.path.join(UPLOAD_FOLDER, test_filename)
        new_solution_path = os.path.join(UPLOAD_FOLDER, solution_filename)

        test_pdf.save(new_test_path)
        solution_pdf.save(new_solution_path)

        questions = extract_question_regions(new_test_path)
        solutions = extract_solution_regions(new_solution_path)

        zip_path = os.path.join(OUTPUT_FOLDER, "screenshots.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for q in questions:
                q_num = q['number']
                q_str = str(q_num).zfill(3)
                base_name = f"{level}_{month}_{year}_{paper_type}_{q_str}"
                question_img_path = os.path.join(OUTPUT_FOLDER, f"{base_name}.png")
                create_cropped_screenshot(new_test_path, q['page'], q['question_bbox'], question_img_path)
                zipf.write(question_img_path, os.path.basename(question_img_path))
                for idx, choice in enumerate(q['choices']):
                    choice_img_path = os.path.join(OUTPUT_FOLDER, f"{base_name}_{idx+1}.png")
                    create_cropped_screenshot(new_test_path, q['page'], choice['bbox'], choice_img_path)
                    zipf.write(choice_img_path, os.path.basename(choice_img_path))
                solution = next((s for s in solutions if s['number'] == q_num), None)
                if solution:
                    solution_img_path = os.path.join(OUTPUT_FOLDER, f"{base_name}_solution.png")
                    create_cropped_screenshot(new_solution_path, solution['page'], solution['solution_bbox'], solution_img_path)
                    zipf.write(solution_img_path, os.path.basename(solution_img_path))
        return send_file(zip_path, as_attachment=True)
    return render_template("index.html")

if __name__ == '__main__':
    app.run(debug=True)
