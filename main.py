import os
import zipfile
import fitz  # PyMuPDF
from flask import Flask, request, send_file, render_template
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
SCREENSHOTS_FOLDER = 'screenshots'
RESULT_FOLDER = 'result'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SCREENSHOTS_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)

def crop_and_save_image(page, bbox, save_path):
    mat = fitz.Matrix(4, 4)
    pix = page.get_pixmap(matrix=mat, clip=fitz.Rect(bbox))
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img.save(save_path)

def process_pdfs(test_path, sol_path, metadata):
    test_pdf = fitz.open(test_path)
    sol_pdf = fitz.open(sol_path)

    base_name = f"{metadata['level']}_{metadata['month']}_{metadata['year']}_{metadata['type']}"

    zip_path = os.path.join(RESULT_FOLDER, f"screenshots_{base_name}.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for q_num in range(1, len(test_pdf) + 1):
            page = test_pdf[q_num - 1]
            sol_page = sol_pdf[q_num - 1] if q_num <= len(sol_pdf) else None

            base_number = (q_num - 1) * 10
            padding = 6

            def padded(num):
                return str(num).zfill(padding)

            # Sample bounding boxes (to be replaced with actual coordinates)
            bboxes = {
                'question': (50, 100, 550, 300),
                'choices': [
                    (60, 310, 540, 360),  # A
                    (60, 370, 540, 420),  # B
                    (60, 430, 540, 480),  # C
                    (60, 490, 540, 540),  # D
                ],
                'solution': (50, 100, 550, 300)  # should be set to correct solution area
            }

            # 0 - question
            q_img_name = f"{padded(base_number + 0)}_{base_name}.png"
            q_img_path = os.path.join(SCREENSHOTS_FOLDER, q_img_name)
            crop_and_save_image(page, bboxes['question'], q_img_path)
            zipf.write(q_img_path, arcname=q_img_name)

            # 1-4 - choices A to D
            for i, bbox in enumerate(bboxes['choices']):
                choice_img_name = f"{padded(base_number + 1 + i)}_{base_name}.png"
                choice_img_path = os.path.join(SCREENSHOTS_FOLDER, choice_img_name)
                crop_and_save_image(page, bbox, choice_img_path)
                zipf.write(choice_img_path, arcname=choice_img_name)

            # 5 - solution
            if sol_page:
                sol_img_name = f"{padded(base_number + 5)}_{base_name}.png"
                sol_img_path = os.path.join(SCREENSHOTS_FOLDER, sol_img_name)
                crop_and_save_image(sol_page, bboxes['solution'], sol_img_path)
                zipf.write(sol_img_path, arcname=sol_img_name)

            # 6-9 - blank placeholders
            for i in range(6, 10):
                blank_img_name = f"{padded(base_number + i)}_{base_name}.png"
                blank_img_path = os.path.join(SCREENSHOTS_FOLDER, blank_img_name)
                Image.new('RGB', (100, 100), color='white').save(blank_img_path)
                zipf.write(blank_img_path, arcname=blank_img_name)

    return zip_path

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        test_file = request.files.get('test_pdf')
        sol_file = request.files.get('sol_pdf')

        if not test_file or not sol_file:
            return 'Missing PDF upload', 400

        level = request.form.get('level', 'Unknown')
        month = request.form.get('month', 'Unknown')
        year = request.form.get('year', 'Unknown')
        exam_type = request.form.get('type', 'Unknown')

        test_filename = secure_filename(test_file.filename or 'test.pdf')
        sol_filename = secure_filename(sol_file.filename or 'sol.pdf')

        test_path = os.path.join(UPLOAD_FOLDER, test_filename)
        sol_path = os.path.join(UPLOAD_FOLDER, sol_filename)

        test_file.save(test_path)
        sol_file.save(sol_path)

        zip_path = process_pdfs(test_path, sol_path, {
            'level': level,
            'month': month,
            'year': year,
            'type': exam_type
        })

        return send_file(zip_path, as_attachment=True)

    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
