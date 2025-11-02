import os
import math
import base64
import io
import sqlite3
from PIL import Image, ImageDraw, ImageFont

DATABASE = 'database.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def get_or_download_font(font_path="arial.ttf", font_size=50):
    if not os.path.exists(font_path):
        try:
            import requests
            response = requests.get("https://github.com/kavin808/arial.ttf/raw/refs/heads/master/arial.ttf", timeout=30)
            response.raise_for_status()
            with open(font_path, 'wb') as f: f.write(response.content)
        except Exception: return ImageFont.load_default()
    try: return ImageFont.truetype(font_path, size=font_size)
    except IOError: return ImageFont.load_default()

def draw_dashed_line(draw, p1, p2, fill, width, dash_length, gap_length):
    """Draws a dashed line between two points."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    length = (dx**2 + dy**2)**0.5
    if length == 0:
        return
    
    dx /= length
    dy /= length
    
    current_pos = 0
    while current_pos < length:
        start = current_pos
        end = current_pos + dash_length
        if end > length:
            end = length
        
        draw.line(
            (p1[0] + start * dx, p1[1] + start * dy, 
             p1[0] + end * dx, p1[1] + end * dy),
            fill=fill,
            width=width
        )
        current_pos += dash_length + gap_length

def draw_dashed_rectangle(draw, box, fill, width, dash_length, gap_length):
    """Draws a dashed rectangle."""
    x0, y0, x1, y1 = box
    # Top
    draw_dashed_line(draw, (x0, y0), (x1, y0), fill, width, dash_length, gap_length)
    # Right
    draw_dashed_line(draw, (x1, y0), (x1, y1), fill, width, dash_length, gap_length)
    # Bottom
    draw_dashed_line(draw, (x1, y1), (x0, y1), fill, width, dash_length, gap_length)
    # Left
    draw_dashed_line(draw, (x0, y1), (x0, y0), fill, width, dash_length, gap_length)

def create_a4_pdf_from_images(image_info, base_folder, output_filename, images_per_page, output_folder=None, orientation='portrait', grid_rows=None, grid_cols=None, practice_mode='none', return_bytes=False):
    if not image_info:
        return False

    A4_WIDTH_PX, A4_HEIGHT_PX = 4960, 7016
    font_large = get_or_download_font(font_size=60)
    font_small = get_or_download_font(font_size=45)

    pages = []
    info_chunks = [image_info[i:i + images_per_page] for i in range(0, len(image_info), images_per_page)]

    for chunk in info_chunks:
        if orientation == 'landscape':
            page_width, page_height = A4_HEIGHT_PX, A4_WIDTH_PX
        else:
            page_width, page_height = A4_WIDTH_PX, A4_HEIGHT_PX
        
        page = Image.new('RGB', (page_width, page_height), 'white')
        draw = ImageDraw.Draw(page)

        is_practice_mode = practice_mode != 'none'

        if grid_rows and grid_cols:
            rows, cols = grid_rows, grid_cols
        else:
            # Default grid calculation
            if len(chunk) > 0:
                cols = int(math.ceil(math.sqrt(len(chunk))))
                rows = int(math.ceil(len(chunk) / cols))
            else:
                rows, cols = 1, 1

        cell_width = (page_width - 400) // cols
        cell_height = (page_height - 400) // rows

        if is_practice_mode:
            cell_width = (page_width - 400) // 2  # Use half the page for the question

        for i, info in enumerate(chunk):
            col = i % cols
            row = i // cols

            if practice_mode == 'portrait_2_spacious':
                section_height = page_height // 2
                cell_x = 200
                cell_y = 200 + (i % 2) * section_height
                cell_height = section_height - 200
            else:
                cell_x = 200 + col * cell_width
                cell_y = 200 + row * cell_height

            try:
                img = None
                if info.get('image_data'):
                    # Handle base64 encoded image data
                    header, encoded = info['image_data'].split(",", 1)
                    image_data = base64.b64decode(encoded)
                    img = Image.open(io.BytesIO(image_data)).convert("RGB")
                elif info.get('processed_filename') or info.get('filename'):
                    # Handle image from file path
                    img_path = os.path.join(base_folder, info.get('processed_filename') or info.get('filename'))
                    if os.path.exists(img_path):
                        img = Image.open(img_path).convert("RGB")

                if img:
                    # Define target dimensions based on mode
                    if practice_mode == 'portrait_2_spacious':
                        target_w = (page_width // 2) - 250  # Half page width with padding
                        target_h = (page_height // 2) - 250 # Half page height with padding
                    elif is_practice_mode:
                        target_w = cell_width - 40
                        target_h = cell_height - 170
                    else:
                        target_w = cell_width - 40
                        target_h = cell_height - 170

                    # Calculate new dimensions while maintaining aspect ratio
                    img_ratio = img.width / img.height
                    target_ratio = target_w / target_h
                    
                    if img_ratio > target_ratio:
                        new_w = int(target_w)
                        new_h = int(new_w / img_ratio)
                    else:
                        new_h = int(target_h)
                        new_w = int(new_h * img_ratio)

                    # For spacious mode, scale up if smaller than 1/12 of page area
                    if practice_mode == 'portrait_2_spacious':
                        page_area = page_width * page_height
                        if new_w * new_h < page_area / 12:
                            scale_factor = math.sqrt((page_area / 12) / (new_w * new_h))
                            scaled_w = int(new_w * scale_factor)
                            scaled_h = int(new_h * scale_factor)
                            # Only apply if it doesn't exceed the target dimensions
                            if scaled_w <= target_w and scaled_h <= target_h:
                                new_w, new_h = scaled_w, scaled_h

                    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    
                    paste_x = cell_x + 20
                    if is_practice_mode and practice_mode != 'portrait_2_spacious':
                        paste_x = 200 # Align to the left for practice modes

                    paste_position = (paste_x, cell_y + 150)
                    page.paste(img, paste_position)

                    # Draw a dashed bounding box for cutting only if not in practice mode
                    if not is_practice_mode:
                        x0, y0 = paste_position
                        x1, y1 = x0 + new_w, y0 + new_h
                        draw_dashed_rectangle(draw, [x0, y0, x1, y1], fill="gray", width=3, dash_length=20, gap_length=15)

                text_x = cell_x + 20
                if is_practice_mode and practice_mode != 'portrait_2_spacious':
                    text_x = 200 # Align to the left for practice modes

                draw.text((text_x, cell_y + 20), f"Q: {info['question_number']}", fill="black", font=font_large)
                info_text = f"Status: {info['status']} | Marked: {info['marked_solution']} | Correct: {info['actual_solution']}"
                draw.text((text_x, cell_y + 90), info_text, fill="darkgray", font=font_small)

            except Exception as e:
                print(f"Error processing image for PDF: {e}")
        
        pages.append(page)

    if pages:
        if return_bytes:
            pdf_bytes = io.BytesIO()
            pages[0].save(pdf_bytes, "PDF", resolution=900.0, save_all=True, append_images=pages[1:])
            return pdf_bytes.getvalue()
        elif output_folder and output_filename:
            output_path = os.path.join(output_folder, output_filename)
            pages[0].save(output_path, "PDF", resolution=900.0, save_all=True, append_images=pages[1:])
            return True
    
    return False