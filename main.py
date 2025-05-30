from flask import Flask, request, send_file, render_template
import os
import pdfplumber
from PyPDF2 import PdfReader
from PIL import Image
import zipfile
from werkzeug.utils import secure_filename

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "output"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        test_pdf = request.files["test_pdf"]
        sol_pdf = request.files["sol_pdf"]
        test_path = os.path.join(UPLOAD_FOLDER, secure_filename(test_pdf.filename))
        sol_path = os.path.join(UPLOAD_FOLDER, secure_filename(sol_pdf.filename))
        test_pdf.save(test_path)
        sol_pdf.save(sol_path)

        with pdfplumber.open(test_path) as pdf:
            text = pdf.pages[0].extract_text()
        year = next((word for word in text.split() if word.isdigit() and len(word) == 4), "UnknownYear")
        months = {"January": "Jan", "February": "Feb", "March": "Mar", "April": "Apr"}
        month = next((months[m] for m in months if m in text), "UnknownMonth")
        type_ = "Reg" if "Regional" in text else "Inv"
        level = next((lvl.replace(" ", "") for lvl in ["Algebra 1", "Geometry", "Algebra 2", "Precalculus", "Calculus", "Statistics"] if lvl in text), "UnknownLevel")
        indiv = "Indiv" if "Individual" in text else "Team" if "Team" in text else "Unknown"

        base_name = f"{year}_{month}_{type_}_{level}_{indiv}"
        output_dir = os.path.join(OUTPUT_FOLDER, base_name)
        os.makedirs(output_dir, exist_ok=True)

        os.rename(test_path, os.path.join(output_dir, os.path.basename(test_path)))
        os.rename(sol_path, os.path.join(output_dir, os.path.basename(sol_path)))

        with pdfplumber.open(test_path) as pdf:
            for i, page in enumerate(pdf.pages[:5]):
                image = page.to_image(resolution=150)
                filename = f"{base_name}_{10 + i:06d}.png"
                image.save(os.path.join(output_dir, filename))

        zip_path = os.path.join(OUTPUT_FOLDER, base_name + ".zip")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for file in os.listdir(output_dir):
                zipf.write(os.path.join(output_dir, file), arcname=file)

        return send_file(zip_path, as_attachment=True)

    return render_template("index.html")


