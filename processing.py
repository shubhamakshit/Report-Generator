
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
from api_key_manager import get_api_key_manager

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
        
        if resized_image.mode == 'RGBA':
            resized_image = resized_image.convert('RGB')

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
    # Get API key from the manager
    manager = get_api_key_manager()
    api_key, key_index = manager.get_key('nvidia')
    
    if not api_key:
        raise Exception("No available NVIDIA API keys. Please set NVIDIA_API_KEY environment variable.")

    NIM_HEADERS = {
        "Authorization": f"Bearer {api_key}",
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
        result = response.json()
        manager.mark_success('nvidia', key_index)
        return result
    except requests.exceptions.RequestException as e:
        manager.mark_failure('nvidia', key_index)
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

def create_pdf_from_full_images(image_paths, output_filename, resolution=100.0):
    """Creates a PDF from a list of full-page images."""
    if not image_paths:
        return False

    try:
        # Open first image to start the PDF
        first_image = Image.open(image_paths[0]).convert('RGB')
    except Exception as e:
        print(f"Error opening first image to get dimensions: {e}")
        return False
    
    pages_to_append = []
    for image_path in image_paths[1:]:
        try:
            page = Image.open(image_path).convert('RGB')
            pages_to_append.append(page)
        except Exception as e:
            print(f"Error opening image {image_path}: {e}")

    try:
        first_image.save(
            output_filename,
            "PDF",
            resolution=resolution,
            save_all=True,
            append_images=pages_to_append
        )
        return True
    except Exception as e:
        print(f"Error saving final PDF: {e}")
        return False

def remove_color_from_image(image_path, target_colors, threshold, bg_mode, region_box=None):
    """
    Removes specific colors from an image using CIELAB Delta E distance.
    Uses manual RGB->Lab conversion to strictly match frontend JS logic (Standard CIELAB).
    """
    # Read image (OpenCV loads as BGR)
    img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")

    # Handle Alpha Channel
    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    
    # 1. PREPARE IMAGE (BGR -> RGB -> Normalized Float)
    # We work on a copy for calculation
    img_bgr = img[:, :, :3]
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    
    # Normalize to 0-1 for formula consistency with typical JS/CSS definitions
    # (Frontend JS might be using 0-255 raw, let's verify frontend code provided earlier)
    # Frontend code: r = rgb[0] / 255 ...
    # Yes, frontend normalizes.
    rgb_norm = img_rgb.astype(np.float32) / 255.0
    
    # 2. RGB to XYZ (Vectorized)
    # Formula matches JS: r = (r > 0.04045) ? ...
    mask_linear = rgb_norm > 0.04045
    rgb_linear = np.where(mask_linear, np.power((rgb_norm + 0.055) / 1.055, 2.4), rgb_norm / 12.92)
    
    R, G, B = rgb_linear[:,:,0], rgb_linear[:,:,1], rgb_linear[:,:,2]
    
    X = R * 0.4124 + G * 0.3576 + B * 0.1805
    Y = R * 0.2126 + G * 0.7152 + B * 0.0722
    Z = R * 0.0193 + G * 0.1192 + B * 0.9505
    
    # Scale XYZ
    X /= 0.95047
    Y /= 1.00000
    Z /= 1.08883
    
    # 3. XYZ to Lab
    # Formula: x = (x > 0.008856) ? ...
    xyz_stack = np.stack([X, Y, Z], axis=-1)
    mask_xyz = xyz_stack > 0.008856
    f_xyz = np.where(mask_xyz, np.power(xyz_stack, 1/3), (7.787 * xyz_stack) + 16/116)
    
    fx, fy, fz = f_xyz[:,:,0], f_xyz[:,:,1], f_xyz[:,:,2]
    
    L_chn = (116.0 * fy) - 16.0
    a_chn = 500.0 * (fx - fy)
    b_chn = 200.0 * (fy - fz)
    
    # 4. CALCULATE DISTANCE
    # Threshold mapping matches frontend
    max_delta_e = 110.0 - (float(threshold) * 100.0)
    max_dist_sq = max_delta_e ** 2 

    final_keep_mask = np.zeros(L_chn.shape, dtype=bool)

    if target_colors:
        # Convert Targets (RGB -> Lab) using same math
        # Since targets are few, we can do simple loop or small array
        for c in target_colors:
            # Normalize
            r, g, b = c['r']/255.0, c['g']/255.0, c['b']/255.0
            
            # Linearize
            r = ((r + 0.055) / 1.055) ** 2.4 if r > 0.04045 else r / 12.92
            g = ((g + 0.055) / 1.055) ** 2.4 if g > 0.04045 else g / 12.92
            b = ((b + 0.055) / 1.055) ** 2.4 if b > 0.04045 else b / 12.92
            
            # XYZ
            x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
            y = (r * 0.2126 + g * 0.7152 + b * 0.0722) / 1.00000
            z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883
            
            # Lab
            fx = x ** (1/3) if x > 0.008856 else (7.787 * x) + 16/116
            fy = y ** (1/3) if y > 0.008856 else (7.787 * y) + 16/116
            fz = z ** (1/3) if z > 0.008856 else (7.787 * z) + 16/116
            
            tL = (116.0 * fy) - 16.0
            ta = 500.0 * (fx - fy)
            tb = 200.0 * (fy - fz)
            
            # Dist
            dist_sq = (L_chn - tL)**2 + (a_chn - ta)**2 + (b_chn - tb)**2
            final_keep_mask |= (dist_sq <= max_dist_sq)

    # Handle Region Box
    if region_box:
        h, w = img.shape[:2]
        rx = int(region_box['x'] * w)
        ry = int(region_box['y'] * h)
        rw = int(region_box['w'] * w)
        rh = int(region_box['h'] * h)
        
        # Mask is TRUE everywhere EXCEPT the region (Keep outside)
        region_protection_mask = np.ones(L_chn.shape, dtype=bool)
        # Ensure coords are within bounds
        ry = max(0, ry); rx = max(0, rx)
        if rw > 0 and rh > 0:
            region_protection_mask[ry:ry+rh, rx:rx+rw] = False
        
        final_keep_mask |= region_protection_mask
    
    # Apply Mask to Image
    result = img.copy()
    
    if bg_mode == 'black':
        bg_color = [0, 0, 0, 255]
    elif bg_mode == 'white':
        bg_color = [255, 255, 255, 255]
    else: # transparent
        bg_color = [0, 0, 0, 0]
        
    remove_mask = ~final_keep_mask
    result[remove_mask] = bg_color

    return result
