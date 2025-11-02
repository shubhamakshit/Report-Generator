
import os
import base64
import io
import re
import json
import requests
import cv2
import numpy as np
from PIL import Image
from flask import current_app

# --- NVIDIA NIM Configuration ---
NIM_API_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-ocr-v1"

def resize_image_if_needed(image_path: str) -> bytes:
    """Resizes an image to a maximum of 500x500 pixels and returns bytes."""
    with Image.open(image_path) as image:
        MAX_SIZE = 500
        width, height = image.size
        
        if width > height:
            new_width = min(width, MAX_SIZE)
            new_height = int(height * (new_width / width))
        else:
            new_height = min(height, MAX_SIZE)
            new_width = int(width * (new_height / height))
            
        if new_width > MAX_SIZE:
            new_width = MAX_SIZE
            new_height = int(height * (new_width / width))
        if new_height > MAX_SIZE:
            new_height = MAX_SIZE
            new_width = int(width * (new_height / height))
        
        resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        img_byte_arr = io.BytesIO()
        resized_image.save(img_byte_arr, format='JPEG', quality=85, optimize=True)
        image_bytes = img_byte_arr.getvalue()
        
        base64_size = len(base64.b64encode(image_bytes).decode('utf-8'))
        if base64_size > 180000:
            quality = max(50, int(85 * (180000 / base64_size)))
            img_byte_arr = io.BytesIO()
            resized_image.save(img_byte_arr, format='JPEG', quality=quality, optimize=True)
            image_bytes = img_byte_arr.getvalue()
            
        return image_bytes

def call_nim_ocr_api(image_bytes: bytes):
    """Calls the NVIDIA NIM API to perform OCR on an image."""
    NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY environment variable not set.")

    NIM_HEADERS = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
        
    base64_encoded_data = base64.b64encode(image_bytes)
    base64_string = base64_encoded_data.decode('utf-8')
    
    if len(base64_string) > 180000:
        raise Exception("Image too large. To upload larger images, use the assets API.")
    
    image_url = f"data:image/png;base64,{base64_string}"
    
    payload = {
        "input": [
            {
                "type": "image_url",
                "url": image_url
            }
        ]
    }
    
    try:
        response = requests.post(NIM_API_URL, headers=NIM_HEADERS, json=payload, timeout=300)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if e.response is not None:
            try:
                error_detail = e.response.json().get("error", e.response.text)
            except json.JSONDecodeError:
                error_detail = e.response.text
        raise Exception(f"NIM API Error: {error_detail}")

def extract_question_number_from_ocr_result(ocr_result: dict) -> str:
    """Extracts the question number from the OCR result."""
    try:
        if "data" in ocr_result and len(ocr_result["data"]) > 0:
            text_detections = ocr_result["data"][0].get("text_detections", [])
            content = " ".join([detection["text_prediction"]["text"] for detection in text_detections])
        else:
            content = str(ocr_result)
            
        match = re.search(r'^\s*(\d+)', content)
        if match:
            return match.group(1)
            
        match = re.search(r'(?:^|\s)(?:[Qq][\.:]?\s*|QUESTION\s+)(\d+)', content, re.IGNORECASE)
        if match:
            return match.group(1)
            
        match = re.search(r'^\s*(\d+)[\.\)]', content)
        if match:
            return match.group(1)
            
        return ""
    except (KeyError, IndexError, TypeError):
        return ""

def crop_image_perspective(image_path, points):
    if len(points) < 4: return cv2.imread(image_path)
    img = cv2.imread(image_path)
    if img is None: raise ValueError("Could not read the image file.")
    height, width = img.shape[:2]
    def clamp(val): return max(0.0, min(1.0, val))
    src_points = np.array([[clamp(p.get('x', 0.0)) * width, clamp(p.get('y', 0.0)) * height] for p in points[:4]], dtype=np.float32)
    (tl, tr, br, bl) = src_points
    width_top, width_bottom = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    max_width = int(max(width_top, width_bottom))
    height_right, height_left = np.linalg.norm(tr - br), np.linalg.norm(tl - bl)
    max_height = int(max(height_right, height_left))
    if max_width == 0 or max_height == 0: return img
    dst_points = np.array([[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src_points, dst_points)
    return cv2.warpPerspective(img, matrix, (max_width, max_height))

def create_pdf_from_full_images(image_paths, output_filename):
    """Creates a PDF from a list of full-page images."""
    if not image_paths:
        return False

    try:
        with Image.open(image_paths[0]) as img:
            width, height = img.size
    except Exception as e:
        print(f"Error opening first image to get dimensions: {e}")
        return False

    pdf = Image.new('RGB', (width, height), 'white')
    
    pages_to_append = []
    for image_path in image_paths[1:]:
        try:
            page = Image.open(image_path).convert('RGB')
            pages_to_append.append(page)
        except Exception as e:
            print(f"Error opening image {image_path}: {e}")

    try:
        pdf.save(
            output_filename,
            "PDF",
            resolution=300.0,
            save_all=True,
            append_images=pages_to_append
        )
        return True
    except Exception as e:
        print(f"Error saving final PDF: {e}")
        return False
