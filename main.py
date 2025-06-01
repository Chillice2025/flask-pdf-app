import os
import re
import zipfile

import fitz  # PyMuPDF
import pdfplumber
from flask import Flask, render_template, request, send_file
from PIL import Image
from werkzeug.utils import secure_filename

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
                lines_dict.setdefault(top, []).append(w)

            sorted_tops = sorted(lines_dict.keys())
            current_question_num = None
            choices = []
            current_choice_letter = None
            current_choice_bbox = None
            question_bbox = None

            def save_question():
                if current_question_num and question_bbox:
                    questions.append({
                        'number': current_question_num,
                        'page': page_num,
                        'question_bbox': question_bbox,
                        'choices': choices.copy()
                    })

            for top in sorted_tops:
                line_words = lines_dict[top]
                line_text = " ".join(w['text'] for w in line_words).strip()

                q_match = re.match(question_pattern, line_text)
                c_match = next((letter for letter in choice_letters if re.match(rf'^{letter}[\.\)]\s*', line_text)), None)

                line_bbox = (
                    min(w['x0'] for w in line_words),
                    min(w['top'] for w in line_words),
                    max(w['x1'] for w in line_words),
                    max(w['bottom'] for w in line_words)
                )

                if q_match:
                    save_question()
                    current_question_num = int(q_match.group(1))
                    question_bbox = line_bbox
                    choices = []
                    current_choice_letter = None
                    current_choice_bbox = None

                elif c_match:
                    if current_choice_letter and current_choice_bbox:
                        choices.append({
                            'letter': current_choice_letter,
                            'bbox': current_choice_bbox
                        })
                    current_choice_letter = c_match
                    current_choice_bbox = line_bbox

                elif current_question_num:
                    if question_bbox:
                        question_bbox = (
                            min(question_bbox[0], line_bbox[0]),
                            min(question_bbox[1], line_bbox[1]),
                            max(question_bbox[2], line_bbox[2]),
                            max(question_bbox[3], line_bbox[3])
                        )
                    if current_choice_letter and current_choice_bbox:
                        current_choice_bbox = (
                            min(current_choice_bbox[0], line_bbox[0]),
                            min(current_choice_bbox[1], line_bbox[1]),
                            max(current_choice_bbox[2], line_bbox[2]),
                            max(current_choice_bbox[3], line_bbox[3])
                        )

            if current_choice_letter and current_choice_bbox:
                choices.append({'letter': current_choice_letter, 'bbox': current_choice_bbox})
            save_question()

    for q in questions:
        q['choices'] = [c for c in q['choices'] if c['letter'] in ['A', 'B', 'C', 'D']]
    return questions

def extract_solution_regions(pdf_path):
    solutions = []
    pattern = r'^\s*(\d+)[\.\)]\s*'

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            words = page.extract_words()
            lines_dict = {}
            for w in words:
                top = round(w['top'])
                lines_dict.setdefault(top, []).append(w)

            sorted_tops = sorted(lines_dict.keys())
            current_solution_num = None
            content_lines = []
            solution_bbox = None

            def save_solution():
                if current_solution_num and solution_bbox:
                    solutions.append({
                        'number': current_solution_num,
                        'page': page_num,
                        'solution_bbox': solution_bbox,
                        'content_lines': content_lines.copy()
                    })

            for top in sorted_tops:
                line_words = lines_dict[top]
                line_text = " ".join(w['text'] for w in line_words).strip()
                line_bbox = (
                    min(w['x0'] for w in line_words),
                    min(w['top'] for w in line_words),
                    max(w['x1'] for w in line_words),
                    max(w['bottom'] for w in line_words)
                )

                sol_match = re.match(pattern, line_text)
                if sol_match:
                    save_solution()
                    current_solution_num = int(sol_match.group(1))
                    solution_bbox = line_bbox
                    content_lines = [line_text]
                elif current_solution_num:
                    content_lines.append(line_text)
                    if solution_bbox:
                        solution_bbox = (
                            min(solution_bbox[0], line_bbox[0]),
                            min(solution_bbox[1], line_bbox[1]),
                            max(solution_bbox[2], line_bbox[2]),
                            max(solution_bbox[3], line_bbox[3])
                        )

            save_solution()
    return solutions

def fitz_rect_from_bbox(bbox, zoom=2):
    return fitz.Rect(*(coord * zoom for coord in bbox))

def create_cropped_screenshot(pdf_path, page_num, bbox, output_path, zoom=2):
    if not bbox or any(v is None or isinstance(v, float) and not (v < float('inf')) for v in bbox):
        create_blank_image(output_path)
        return

    doc = fitz.open(pdf_path)
    page = doc[page_num]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", (pix.width, pix.height), bytes(pix.samples))


    rect = fitz_rect_from_bbox(bbox, zoom)
    x0, y0, x1, y1 = map(int, [rect.x0, rect.y0, rect.x1, rect.y1])
    if x1 > x0 and y1 > y0:
        cropped = img.crop((x0, y0, x1, y1))
        cropped.save(output_path)
    else:
        create_blank_image(output_path)
    doc.close()

def create_blank_image(output_path, width=800, height=600):
    Image.new('RGB', (width, height), 'white').save(output_path)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        test_pdf = request.files.get("test_pdf")
        sol_pdf = request.files.get("sol_pdf")
        level = request.form['level']
        month = request.form['month']
        year = request.form['year']
        paper_type = request.form['type']

        if not test_pdf or not sol_pdf:
            return "Missing PDF upload", 400

        test_filename = secure_filename(test_pdf.filename or "test.pdf")
        sol_filename = secure_filename(sol_pdf.filename or "solution.pdf")

        test_path = os.path.join(UPLOAD_FOLDER, test_filename)
        sol_path = os.path.join(UPLOAD_FOLDER, sol_filename)

        test_pdf.save(test_path)
        sol_pdf.save(sol_path)

        questions = extract_question_regions(test_path)
        solutions = extract_solution_regions(sol_path)

        zip_path = os.path.join(OUTPUT_FOLDER, "screenshots.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for q in questions:
                q_num = q['number']
                q_str = str(q_num).zfill(3)
                base_name = f"{level}_{month}_{year}_{paper_type}_{q_str}"
                q_img_path = os.path.join(OUTPUT_FOLDER, f"{base_name}.png")
                create_cropped_screenshot(test_path, q['page'], q['question_bbox'], q_img_path)
                zipf.write(q_img_path, os.path.basename(q_img_path))

                for idx, choice in enumerate(q['choices']):
                    choice_img_path = os.path.join(OUTPUT_FOLDER, f"{base_name}_{idx+1}.png")
                    create_cropped_screenshot(test_path, q['page'], choice['bbox'], choice_img_path)
                    zipf.write(choice_img_path, os.path.basename(choice_img_path))

                solution = next((s for s in solutions if s['number'] == q_num), None)
                if solution:
                    sol_img_path = os.path.join(OUTPUT_FOLDER, f"{base_name}_solution.png")
                    create_cropped_screenshot(sol_path, solution['page'], solution['solution_bbox'], sol_img_path)
                    zipf.write(sol_img_path, os.path.basename(sol_img_path))

        return send_file(zip_path, as_attachment=True)

    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
