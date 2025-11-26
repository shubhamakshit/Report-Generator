import fitz  # PyMuPDF
import sys

def expand_pdf_for_notes(input_pdf, output_pdf, bg_color=(1, 1, 1), mode='notes_only', stitch_direction='horizontal', add_space=True, pattern=None, pattern_color=(0.8, 0.8, 0.8)):
    """
    Expand or rearrange a PDF for note-taking.

    Args:
        input_pdf (str): Path to input PDF file.
        output_pdf (str): Path to output PDF file.
        bg_color (tuple): RGB color for the notes area background.
        mode (str): The processing mode: 'notes_only', 'split', or 'stitch'.
        stitch_direction (str): For 'stitch' mode, how to rearrange columns ('horizontal' or 'vertical').
        add_space (bool): If True, add space for notes.
        pattern (str): Name of the pattern to draw ('grid', 'dots').
        pattern_color (tuple): RGB color for the pattern.
    """
    doc = fitz.open(input_pdf)
    new_doc = fitz.open()

    for page_num in range(len(doc)):
        page = doc[page_num]
        orig_rect = page.rect
        orig_width = orig_rect.width
        orig_height = orig_rect.height

        left_half_clip = fitz.Rect(0, 0, orig_width / 2, orig_height)
        right_half_clip = fitz.Rect(orig_width / 2, 0, orig_width, orig_height)

        if mode == 'split':
            # Create a new page for the left half
            new_page_width = orig_width / 2 if not add_space else orig_width
            left_page = new_doc.new_page(width=new_page_width, height=orig_height)
            left_page.show_pdf_page(fitz.Rect(0, 0, orig_width / 2, orig_height), doc, page_num, clip=left_half_clip)
            if add_space:
                notes_rect = fitz.Rect(orig_width / 2, 0, orig_width, orig_height)
                left_page.draw_rect(notes_rect, color=None, fill=bg_color)
                if pattern:
                    _draw_pattern(new_doc, left_page, notes_rect, pattern, pattern_color)

            # Create a new page for the right half
            right_page = new_doc.new_page(width=new_page_width, height=orig_height)
            right_page.show_pdf_page(fitz.Rect(0, 0, orig_width / 2, orig_height), doc, page_num, clip=right_half_clip)
            if add_space:
                notes_rect = fitz.Rect(orig_width / 2, 0, orig_width, orig_height)
                right_page.draw_rect(notes_rect, color=None, fill=bg_color)
                if pattern:
                    _draw_pattern(new_doc, right_page, notes_rect, pattern, pattern_color)

        elif mode == 'stitch':
            if stitch_direction == 'horizontal':
                new_width = orig_width
                if add_space:
                    new_width *= 2
                new_page = new_doc.new_page(width=new_width, height=orig_height)
                new_page.show_pdf_page(fitz.Rect(0, 0, orig_width / 2, orig_height), doc, page_num, clip=left_half_clip)
                new_page.show_pdf_page(fitz.Rect(orig_width / 2, 0, orig_width, orig_height), doc, page_num, clip=right_half_clip)
                if add_space:
                    notes_rect = fitz.Rect(orig_width, 0, new_width, orig_height)
                    new_page.draw_rect(notes_rect, color=None, fill=bg_color)
                    if pattern:
                        _draw_pattern(new_doc, new_page, notes_rect, pattern, pattern_color)
            
            else:  # vertical
                new_width = orig_width / 2
                if add_space:
                    new_width = orig_width
                new_height = orig_height * 2
                new_page = new_doc.new_page(width=new_width, height=new_height)
                new_page.show_pdf_page(fitz.Rect(0, 0, orig_width / 2, orig_height), doc, page_num, clip=left_half_clip)
                new_page.show_pdf_page(fitz.Rect(0, orig_height, orig_width / 2, new_height), doc, page_num, clip=right_half_clip)
                if add_space:
                    notes_rect = fitz.Rect(orig_width / 2, 0, new_width, new_height)
                    new_page.draw_rect(notes_rect, color=None, fill=bg_color)
                    if pattern:
                        _draw_pattern(new_doc, new_page, notes_rect, pattern, pattern_color)

        elif mode == 'notes_only':
            if add_space:
                new_page = new_doc.new_page(width=orig_width * 2, height=orig_height)
                right_rect = fitz.Rect(orig_width, 0, orig_width * 2, orig_height)
                new_page.draw_rect(right_rect, color=None, fill=bg_color)
                if pattern:
                    _draw_pattern(new_doc, new_page, right_rect, pattern, pattern_color)
                new_page.show_pdf_page(fitz.Rect(0, 0, orig_width, orig_height), doc, page_num)
            else:
                new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

        else: # Default to copying the page if mode is unknown
            new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

    new_doc.save(output_pdf)
    total_pages = len(new_doc)
    new_doc.close()
    doc.close()
    print(f"✓ Successfully created: {output_pdf}")
    print(f"  Pages processed: {total_pages}")

def _draw_pattern(doc, page, rect, pattern, color):
    if pattern == 'grid':
        _draw_grid(page, rect, color=color)
    elif pattern == 'dots':
        _draw_dots(doc, page, rect, color=color)

def _draw_grid(page, rect, spacing=20, color=(0.8, 0.8, 0.8)):
    # Draw vertical lines
    for x in range(int(rect.x0), int(rect.x1), spacing):
        page.draw_line(fitz.Point(x, rect.y0), fitz.Point(x, rect.y1), color=color, width=0.5)
    # Draw horizontal lines
    for y in range(int(rect.y0), int(rect.y1), spacing):
        page.draw_line(fitz.Point(rect.x0, y), fitz.Point(rect.x1, y), color=color, width=0.5)

def _draw_dots(doc, page, rect, spacing=20, radius=1, color=(0.8, 0.8, 0.8)):
    """Creates a tileable dot pattern using a Form XObject for efficiency."""
    # Create a small rectangle for one pattern unit
    stamp_rect = fitz.Rect(0, 0, spacing, spacing)
    # Create a new PDF for the stamp
    stamp_doc = fitz.open() 
    stamp_page = stamp_doc.new_page(width=spacing, height=spacing)

    # Draw a single dot in the corner of the stamp page
    stamp_page.draw_circle(fitz.Point(radius, radius), radius, color=color, fill=color)

    # Convert the stamp page to a stamp (Form XObject) and get its cross-reference number
    stamp_xref = doc.get_xref(stamp_doc.convert_to_pdf())
    stamp_doc.close()

    # Tile the stamp across the target rectangle
    for x in range(int(rect.x0), int(rect.x1), spacing):
        for y in range(int(rect.y0), int(rect.y1), spacing):
            page.show_pdf_page(fitz.Rect(x, y, x + spacing, y + spacing), stamp_xref)



def main():
    """Main function with command-line interface"""
    import argparse
    parser = argparse.ArgumentParser(description="Expand or rearrange a PDF for note-taking.")
    parser.add_argument("input_pdf", help="Path to input PDF file.")
    parser.add_argument("output_pdf", nargs='?', help="Path to output PDF file.")
    parser.add_argument("--mode", choices=['notes_only', 'split', 'stitch'], default='notes_only', help="Processing mode.")
    parser.add_argument("--stitch-direction", choices=['horizontal', 'vertical'], default='horizontal', help="Direction for 'stitch' mode.")
    parser.add_argument("--no-space", action='store_true', help="Don't add extra space for notes.")
    parser.add_argument("--bg", default='white', help="Background color (white, lightgray, cream)." )

    args = parser.parse_args()

    output_pdf = args.output_pdf
    if not output_pdf:
        suffix = f'_{args.mode}'
        if args.mode == 'stitch':
            suffix += f'_{args.stitch_direction[:4]}'
        if not args.no_space:
            suffix += '_notes'
        suffix += '.pdf'
        output_pdf = args.input_pdf.replace('.pdf', suffix)

    bg_colors = {
        'white': (1, 1, 1),
        'lightgray': (0.95, 0.95, 0.95),
        'cream': (1, 0.99, 0.94),
    }
    bg_color = bg_colors.get(args.bg, (1, 1, 1))

    try:
        expand_pdf_for_notes(
            args.input_pdf, 
            output_pdf, 
            bg_color=bg_color, 
            mode=args.mode, 
            stitch_direction=args.stitch_direction, 
            add_space=not args.no_space
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
