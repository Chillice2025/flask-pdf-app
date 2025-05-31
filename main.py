
from flask import Flask, request, send_file, render_template
import os
import pdfplumber
from PyPDF2 import PdfReader
from PIL import Image, ImageDraw
import zipfile
from werkzeug.utils import secure_filename
import re
import fitz  # PyMuPDF for better image handling

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "output"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

def extract_question_regions(pdf_path):
    """Extract question regions from PDF using text analysis"""
    questions = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
                
            # Find question numbers (looking for patterns like "1.", "2)", etc.)
            question_pattern = r'^\s*(\d+)[\.\)]\s*'
            lines = text.split('\n')
            
            current_question = None
            for i, line in enumerate(lines):
                match = re.match(question_pattern, line.strip())
                if match:
                    if current_question:
                        questions.append(current_question)
                    
                    question_num = int(match.group(1))
                    current_question = {
                        'number': question_num,
                        'page': page_num,
                        'text_start': i,
                        'content': line,
                        'choices': []
                    }
                elif current_question and line.strip():
                    # Look for answer choices (A), B), etc.
                    choice_pattern = r'^\s*([A-D])\s*[\)\.]?\s*(.*)'
                    choice_match = re.match(choice_pattern, line.strip())
                    if choice_match:
                        choice_letter = choice_match.group(1)
                        choice_text = choice_match.group(2)
                        current_question['choices'].append({
                            'letter': choice_letter,
                            'text': choice_text,
                            'line': i
                        })
                    else:
                        current_question['content'] += ' ' + line
            
            if current_question:
                questions.append(current_question)
    
    return questions

def find_solution_for_question(sol_pdf_path, question_num):
    """Find the solution for a specific question in the solution PDF"""
    with pdfplumber.open(sol_pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
            
            # Look for question number in solution
            pattern = rf'\b{question_num}[\.\)]\s*'
            if re.search(pattern, text):
                return page_num
    
    return None

def create_question_screenshot(pdf_path, page_num, question_info, output_path):
    """Create screenshot of question without the number"""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    
    # Get page as image
    mat = fitz.Matrix(2, 2)  # 2x zoom for better quality
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    
    # For now, take full page - in a real implementation you'd need to
    # identify text regions and crop accordingly
    img.save(output_path)
    doc.close()

def create_choice_screenshot(pdf_path, page_num, choice_info, output_path):
    """Create screenshot of individual answer choice"""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    
    mat = fitz.Matrix(2, 2)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    
    # For now, take full page - would need region detection for actual cropping
    img.save(output_path)
    doc.close()

def create_solution_screenshot(sol_pdf_path, page_num, output_path):
    """Create screenshot of solution"""
    doc = fitz.open(sol_pdf_path)
    page = doc[page_num]
    
    mat = fitz.Matrix(2, 2)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img.save(output_path)
    doc.close()

def create_blank_image(output_path, width=800, height=600):
    """Create a blank white image"""
    img = Image.new('RGB', (width, height), 'white')
    img.save(output_path)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        test_pdf = request.files["test_pdf"]
        sol_pdf = request.files["sol_pdf"]
        test_path = os.path.join(UPLOAD_FOLDER, secure_filename(test_pdf.filename))
        sol_path = os.path.join(UPLOAD_FOLDER, secure_filename(sol_pdf.filename))
        test_pdf.save(test_path)
        sol_pdf.save(sol_path)

        # Extract text from both PDFs to get better parsing
        with pdfplumber.open(test_path) as pdf:
            test_text = ""
            for page in pdf.pages[:3]:
                page_text = page.extract_text()
                if page_text:
                    test_text += page_text + " "
        
        with pdfplumber.open(sol_path) as pdf:
            sol_text = ""
            for page in pdf.pages[:3]:
                page_text = page.extract_text()
                if page_text:
                    sol_text += page_text + " "
        
        # Combine text for better parsing
        combined_text = test_text + " " + sol_text
        
        # Extract year (look for 4-digit numbers, prefer 20xx years)
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
        
        # Extract type (Regional or Invitational)
        type_ = "Reg" if "Regional" in combined_text else "Inv" if "Invitational" in combined_text else "Inv"
        
        # Extract level
        level = "UnknownLevel"
        for lvl in ["Algebra 1", "Geometry", "Algebra 2", "Precalculus", "Calculus", "Statistics"]:
            if lvl in combined_text:
                level = lvl.replace(" ", "")
                break
        
        # Extract individual vs team
        indiv = "Indiv" if "Individual" in combined_text else "Team" if "Team" in combined_text else "Unknown"

        base_name = f"{year}_{month}_{type_}_{level}_{indiv}"
        output_dir = os.path.join(OUTPUT_FOLDER, base_name)
        os.makedirs(output_dir, exist_ok=True)

        new_test_path = os.path.join(output_dir, os.path.basename(test_path))
        new_sol_path = os.path.join(output_dir, os.path.basename(sol_path))
        os.rename(test_path, new_test_path)
        os.rename(sol_path, new_sol_path)

        # Extract questions from test PDF
        questions = extract_question_regions(new_test_path)
        
        # Process each question
        for question in questions:
            q_num = question['number']
            
            # Calculate base number for this question (question 1 = 00010, question 2 = 00100, etc.)
            if q_num < 10:
                base_num = q_num * 10
            else:
                base_num = q_num * 100
            
            # 1. Question screenshot (without number) - 00010, 00100, etc.
            question_filename = f"{base_name}_{base_num:06d}.png"
            create_question_screenshot(new_test_path, question['page'], question, 
                                     os.path.join(output_dir, question_filename))
            
            # 2. Answer choice screenshots (A-D) - 00011-00014, 00101-00104, etc.
            choice_letters = ['A', 'B', 'C', 'D']
            for i, letter in enumerate(choice_letters):
                choice_filename = f"{base_name}_{base_num + i + 1:06d}.png"
                # Find the choice in the question
                choice_found = False
                for choice in question['choices']:
                    if choice['letter'] == letter:
                        create_choice_screenshot(new_test_path, question['page'], choice,
                                               os.path.join(output_dir, choice_filename))
                        choice_found = True
                        break
                
                if not choice_found:
                    # Create blank image if choice not found
                    create_blank_image(os.path.join(output_dir, choice_filename))
            
            # 3. Solution screenshot - 00015, 00105, etc.
            solution_filename = f"{base_name}_{base_num + 5:06d}.png"
            sol_page = find_solution_for_question(new_sol_path, q_num)
            if sol_page is not None:
                create_solution_screenshot(new_sol_path, sol_page, 
                                         os.path.join(output_dir, solution_filename))
            else:
                create_blank_image(os.path.join(output_dir, solution_filename))
            
            # 4. Four blank screenshots - 00016-00019, 00106-00109, etc.
            for i in range(4):
                blank_filename = f"{base_name}_{base_num + 6 + i:06d}.png"
                create_blank_image(os.path.join(output_dir, blank_filename))

        zip_path = os.path.join(OUTPUT_FOLDER, base_name + ".zip")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for file in os.listdir(output_dir):
                if file.endswith('.png'):
                    zipf.write(os.path.join(output_dir, file), arcname=file)

        return send_file(zip_path, as_attachment=True)

    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
