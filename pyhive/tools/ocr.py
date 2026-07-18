import subprocess, os, platform
import shutil, json, msgpack
import threading, pathlib

import os
import pytesseract
from pytesseract import Output
from PIL import Image
from functools import wraps

# ==========================================
# Configuration
# ==========================================
# Update this path if Tesseract is installed elsewhere
TESSERACT_EXE_PATH = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE_PATH

# ==========================================
# Custom Decorator
# ==========================================
def process_image_safely(func):
    """
    Decorator that handles file validation, image loading, and error handling.
    It passes the opened PIL Image to the wrapped function.
    """
    @wraps(func)
    def wrapper(image_path, *args, **kwargs):
        if not os.path.exists(image_path):
            return {"status": "error", "message": f"File not found: {image_path}"}
        
        try:
            with Image.open(image_path) as img:
                # Pass the opened image to the target function
                result = func(img, *args, **kwargs)
                return {"status": "success", "data": result}
                
        except pytesseract.TesseractNotFoundError:
            return {"status": "error", "message": "tesseract.exe not found. Check your path."}
        except pytesseract.TesseractError as e:
            return {"status": "error", "message": f"Tesseract engine error: {e}"}
        except Exception as e:
            return {"status": "error", "message": f"Unexpected error: {e}"}
            
    return wrapper

# ==========================================
# Core Callable Functions
# ==========================================

@process_image_safely
def extract_plain_text(img, lang='eng', psm=3):
    """Extracts raw string text from the image."""
    custom_config = f'--psm {psm}'
    return pytesseract.image_to_string(img, lang=lang, config=custom_config).strip()

@process_image_safely
def extract_character_boxes(img, lang='eng'):
    """Returns bounding box coordinates for individual characters."""
    return pytesseract.image_to_boxes(img, lang=lang)

@process_image_safely
def extract_detailed_data(img, lang='eng', min_conf=0):
    """
    Extracts detailed word-level data (coordinates, confidence scores).
    Filters out results below the min_conf threshold and empty strings.
    """
    raw_data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT)
    
    filtered_results = []
    n_boxes = len(raw_data['text'])
    
    for i in range(n_boxes):
        conf = int(raw_data['conf'][i])
        text = raw_data['text'][i].strip()
        
        if conf >= min_conf and text:
            filtered_results.append({
                "text": text,
                "confidence": conf,
                "left": raw_data['left'][i],
                "top": raw_data['top'][i],
                "width": raw_data['width'][i],
                "height": raw_data['height'][i]
            })
            
    return filtered_results

@process_image_safely
def detect_orientation_and_script(img):
    """
    Detects the rotation angle and script (e.g., Latin, Cyrillic) of the text.
    Returns a dictionary with rotation, orientation, and script details.
    """
    return pytesseract.image_to_osd(img, output_type=Output.DICT)

@process_image_safely
def export_to_searchable_pdf(img, output_filename="output.pdf", lang='eng'):
    """Converts the image into a searchable PDF file."""
    pdf_bytes = pytesseract.image_to_pdf_or_hocr(img, extension='pdf', lang=lang)
    
    with open(output_filename, 'wb') as f:
        f.write(pdf_bytes)
        
    return f"PDF saved successfully as {output_filename}"

@process_image_safely
def export_to_hocr(img, output_filename="output.hocr", lang='eng'):
    """Converts the image into HOCR format (HTML layout data)."""
    hocr_bytes = pytesseract.image_to_pdf_or_hocr(img, extension='hocr', lang=lang)
    
    with open(output_filename, 'wb') as f:
        f.write(hocr_bytes)
        
    return f"HOCR saved successfully as {output_filename}"


if __name__ == "__main__":
    test_image = "sample_document.png" # Replace with a real image path
    
    # 1. Plain Text
    print("--- 1. Plain Text ---")
    text_result = extract_plain_text(test_image)
    print(text_result)

    # 2. Detailed Data (Words with > 80% confidence)
    print("\n--- 2. Detailed Data (High Confidence) ---")
    data_result = extract_detailed_data(test_image, min_conf=80)
    if data_result["status"] == "success":
        for item in data_result["data"]:
            print(f"[{item['confidence']}%] {item['text']} at (x:{item['left']}, y:{item['top']})")

    # 3. Orientation Detection
    print("\n--- 3. OSD (Orientation and Script) ---")
    osd_result = detect_orientation_and_script(test_image)
    print(osd_result)

    # 4. Generate PDF
    print("\n--- 4. Searchable PDF ---")
    pdf_result = export_to_searchable_pdf(test_image, output_filename="searchable_scan.pdf")
    print(pdf_result)