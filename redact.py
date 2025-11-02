# main_redaction_processor.py

# Required packages: pip install requests Pillow
import os
import requests
from PIL import Image, ImageDraw
import io
import base64
import json

# --- Configuration ---
# API endpoints should remain constant
INVOKE_URL_OCR = "https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-ocr-v1"
INVOKE_URL_PARSER = "https://integrate.api.nvidia.com/v1/chat/completions"

# Define a max pixel count for the parser model to avoid sending overly large images.
MAX_PIXELS_FOR_PARSER = 1024 * 1024 # 1 Megapixel

# --- Internal Helper Functions ---

def _get_average_color_from_regions(image: Image.Image, regions: list[tuple]):
    """Calculates the average RGB color from a list of regions in an image."""
    total_r, total_g, total_b = 0, 0, 0
    pixel_count = 0
    img_width, img_height = image.size
    if image.mode == 'RGBA': image = image.convert('RGB')
    pixels = image.load()
    for region in regions:
        x1, y1, x2, y2 = [max(0, int(c)) for c in region]
        x2 = min(img_width, x2); y2 = min(img_height, y2)
        for x in range(x1, x2):
            for y in range(y1, y2):
                r, g, b = pixels[x, y]
                total_r += r; total_g += g; total_b += b
                pixel_count += 1
    if pixel_count == 0: return (0, 0, 0)
    return (total_r // pixel_count, total_g // pixel_count, total_b // pixel_count)


def _detect_pictures_with_parser(image_to_process: Image.Image, api_key: str):
    """Sends an image to the NemoRetriever Parser model to detect 'Picture' elements."""
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    buffered = io.BytesIO()
    image_to_process.save(buffered, format="PNG")
    b64_str = base64.b64encode(buffered.getvalue()).decode("ascii")
    
    content = f'<img src="data:image/png;base64,{b64_str}" />'
    tool_name = "markdown_bbox"
    payload = {
        "model": "nvidia/nemoretriever-parse",
        "messages": [{"role": "user", "content": content}],
        "tools": [{"type": "function", "function": {"name": tool_name}}],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
        "max_tokens": 2048,
    }

    response = requests.post(INVOKE_URL_PARSER, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    response_json = response.json()
    
    picture_bboxes = []
    tool_calls = response_json.get('choices', [{}])[0].get('message', {}).get('tool_calls', [])
    if tool_calls:
        arguments_str = tool_calls[0].get('function', {}).get('arguments', '[]')
        parsed_arguments = json.loads(arguments_str)
        if parsed_arguments and isinstance(parsed_arguments, list):
            for element in parsed_arguments[0]:
                if element.get("type") == "Picture" and element.get("bbox"):
                    picture_bboxes.append(element["bbox"])
    return picture_bboxes


def _redact_text_in_image(input_image: Image.Image, api_key: str):
    """Sends a (cropped) image to the OCR model and returns a redacted version."""
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    buffered = io.BytesIO()
    input_image.save(buffered, format="PNG")
    image_b64 = base64.b64encode(buffered.getvalue()).decode()
    
    payload = {"input": [{"type": "image_url", "url": f"data:image/png;base64,{image_b64}"}]}
    try:
        response = requests.post(INVOKE_URL_OCR, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        response_json = response.json()
    except requests.exceptions.RequestException: return input_image

    image_with_redactions = input_image.copy()
    draw = ImageDraw.Draw(image_with_redactions)
    img_width, img_height = image_with_redactions.size
    radius = max(1, int(((img_width**2 + img_height**2)**0.5) / 100))
    
    try:
        detections = response_json['data'][0]['text_detections']
        for detection in detections:
            bbox = detection.get("bounding_box")
            if bbox and bbox.get("points"):
                points = bbox["points"]
                p1 = (points[0]['x'] * img_width, points[0]['y'] * img_height)
                p3 = (points[2]['x'] * img_width, points[2]['y'] * img_height)
                sample_regions = [(p1[0], p1[1] - radius, p3[0], p1[1]), (p1[0], p3[1], p3[0], p3[1] + radius), (p1[0] - radius, p1[1], p1[0], p3[1]), (p3[0], p1[1], p3[0] + radius, p3[1])]
                redaction_color = _get_average_color_from_regions(image_with_redactions, sample_regions)
                draw.rectangle([p1, p3], fill=redaction_color)
        return image_with_redactions
    except (KeyError, IndexError, TypeError): return input_image


# --- Main Public Function ---

def redact_pictures_in_image(image_source: str, api_key: str, callback: callable = None) -> Image.Image:
    """
    Analyzes an image to find pictures, then redacts text within those pictures.

    Args:
        image_source (str): The source of the image. Can be a local file path
                            or a base64 encoded string.
        api_key (str): Your NVIDIA API key.
        callback (callable, optional): A function to call with progress updates.
                                       Defaults to None. The function should accept
                                       a single string argument.

    Returns:
        Image.Image: A PIL Image object with the text inside pictures redacted.
    """
    
    def _progress(message: str):
        if callback:
            callback(message)

    _progress("Step 1: Loading image...")
    try:
        if os.path.exists(image_source):
            input_image = Image.open(image_source).convert("RGB")
        else:
            image_bytes = base64.b64decode(image_source)
            input_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        raise ValueError(f"Invalid image_source: not a valid file path or base64 string. Error: {e}")

    # --- Resize if necessary for analysis ---
    image_to_analyze = input_image
    original_width, original_height = input_image.size
    if (original_width * original_height) > MAX_PIXELS_FOR_PARSER:
        _progress(f"Image is large, resizing for initial analysis...")
        scale = (MAX_PIXELS_FOR_PARSER / (original_width * original_height))**0.5
        new_dims = (int(original_width * scale), int(original_height * scale))
        image_to_analyze = input_image.resize(new_dims, Image.Resampling.LANCZOS)
    
    # --- Detect Pictures ---
    _progress("Step 2: Detecting 'Picture' elements...")
    try:
        picture_bboxes = _detect_pictures_with_parser(image_to_analyze, api_key)
    except requests.exceptions.RequestException as e:
        _progress(f"API Error during picture detection: {e}")
        raise  # Re-raise the exception after reporting progress

    if not picture_bboxes:
        _progress("No 'Picture' elements were found. Returning original image.")
        return input_image

    _progress(f"Step 3: Found {len(picture_bboxes)} 'Picture' element(s). Redacting text...")
    final_image = input_image.copy()
    
    # --- Crop, Redact, and Paste ---
    for i, box in enumerate(picture_bboxes):
        _progress(f"  - Processing picture {i + 1} of {len(picture_bboxes)}...")
        x1 = int(box["xmin"] * original_width)
        y1 = int(box["ymin"] * original_height)
        x2 = int(box["xmax"] * original_width)
        y2 = int(box["ymax"] * original_height)
        
        # Crop from the original, high-resolution image
        cropped_element = input_image.crop((x1, y1, x2, y2))
        
        redacted_crop = _redact_text_in_image(cropped_element, api_key)
        
        # Paste the redacted, high-resolution crop back
        final_image.paste(redacted_crop, (x1, y1))
        
    _progress("Step 4: Redaction process complete.")
    return final_image


# --- Example Usage ---
if __name__ == "__main__":
    
    # Define a simple callback function to print progress to the console.
    def print_progress(message: str):
        print(f"[PROGRESS] {message}")

    # 1. Get API Key from environment variable
    my_api_key = os.getenv("NVIDIA_API_KEY")
    if not my_api_key:
        print("ERROR: Please set the NVIDIA_API_KEY environment variable.")
    else:
        # 2. Define the path to your input image
        #    (replace with your actual image file)
        input_image_path = "yolox1.png" # Make sure this image exists
        
        if not os.path.exists(input_image_path):
             print(f"ERROR: Input image not found at '{input_image_path}'")
        else:
            print("--- Running Redaction on Image Path ---")
            try:
                # 3. Call the main function with the image path and callback
                redacted_image = redact_pictures_in_image(
                    image_source=input_image_path,
                    api_key=my_api_key,
                    callback=print_progress
                )

                # 4. Save the result
                output_path = "redacted_output.png"
                redacted_image.save(output_path)
                print(f"\nSuccessfully saved redacted image to '{output_path}'")

            except Exception as e:
                print(f"\nAn error occurred: {e}")

