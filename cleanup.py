
import sqlite3
import os
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table

# --- Configuration ---
DB_PATH = 'database.db'
UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'
OUTPUT_FOLDER = 'output'
OLDER_THAN_DAYS = 5
DRY_RUN = True  # Set to False to perform actual deletion

# --- Immunity Reasons ---
REASON_PERSISTED = "Persisted"
REASON_NEETPREP = "NeetPrep/JSON"
REASON_CLASSIFIED = "Classified"
REASON_RECENT = "Too Recent"

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def is_classified_session(conn, session_id):
    """Checks if a session contains any classified questions."""
    if not session_id:
        return False
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM questions 
        WHERE session_id = ? AND subject IS NOT NULL AND chapter IS NOT NULL 
        LIMIT 1
    """, (session_id,))
    return cursor.fetchone() is not None

def show_disk_usage_report(console):
    """Calculates and displays a report of disk usage by category."""
    console.print("\n[bold cyan]Disk Usage Report[/bold cyan]")
    
    def sizeof_fmt(num, suffix="B"):
        """Formats a size in bytes to a human-readable string."""
        for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
            if abs(num) < 1024.0:
                return f"{num:3.1f}{unit}{suffix}"
            num /= 1024.0
        return f"{num:.1f}Y{suffix}"

    # --- Summary Report ---
    usage_data = {}
    folders_to_scan = {
        "Uploaded Originals": UPLOAD_FOLDER,
        "Processed Images": PROCESSED_FOLDER,
        "Generated PDFs": OUTPUT_FOLDER,
    }

    for category, folder in folders_to_scan.items():
        total_size = 0
        file_count = 0
        try:
            for dirpath, _, filenames in os.walk(folder):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        try:
                            total_size += os.path.getsize(fp)
                            file_count += 1
                        except FileNotFoundError:
                            pass
        except FileNotFoundError:
            pass 
        usage_data[category] = {"size": total_size, "count": file_count}
        
    summary_table = Table(title="Disk Space Usage by Category")
    summary_table.add_column("Category", style="cyan")
    summary_table.add_column("File Count", style="magenta", justify="right")
    summary_table.add_column("Total Size", style="green", justify="right")

    total_size_all = 0
    total_count_all = 0
    for category, data in usage_data.items():
        summary_table.add_row(category, str(data["count"]), sizeof_fmt(data["size"]))
        total_size_all += data["size"]
        total_count_all += data["count"]

    summary_table.add_section()
    summary_table.add_row("Total", f"[bold]{total_count_all}[/bold]", f"[bold]{sizeof_fmt(total_size_all)}[/bold]")
    
    console.print(summary_table)

    # --- Detailed Breakdown for Uploaded Originals ---
    console.print("\n[bold]Breakdown of 'Uploaded Originals':[/bold]")
    
    conn = get_db_connection()
    sessions = conn.execute('SELECT id, original_filename FROM sessions').fetchall()
    
    session_sizes = []
    with console.status("[cyan]Calculating size per session...[/cyan]"):
        for session in sessions:
            session_id = session['id']
            images = conn.execute("SELECT filename FROM images WHERE session_id = ? AND image_type = 'original'", (session_id,)).fetchall()
            
            total_size = 0
            file_count = 0
            for img in images:
                if not img['filename']: continue
                try:
                    fp = os.path.join(UPLOAD_FOLDER, img['filename'])
                    if not os.path.islink(fp):
                        total_size += os.path.getsize(fp)
                        file_count += 1
                except FileNotFoundError:
                    pass # File may not exist, that's okay
            
            if file_count > 0:
                session_sizes.append({
                    "id": session_id,
                    "name": session['original_filename'],
                    "size": total_size,
                    "count": file_count
                })

    # Sort sessions by size, descending
    session_sizes.sort(key=lambda x: x['size'], reverse=True)

    breakdown_table = Table(show_header=True, header_style="bold magenta")
    breakdown_table.add_column("Session ID", style="dim", min_width=15)
    breakdown_table.add_column("Original Filename", style="cyan", min_width=30)
    breakdown_table.add_column("File Count", style="magenta", justify="right")
    breakdown_table.add_column("Total Size", style="green", justify="right")

    for session_data in session_sizes:
        breakdown_table.add_row(
            session_data['id'],
            session_data['name'],
            str(session_data['count']),
            sizeof_fmt(session_data['size'])
        )
        
    console.print(breakdown_table)
    conn.close()



def main():
    """Main function to identify and clean up old data."""
    console = Console()
    console.print(f"[bold cyan]Starting Cleanup Process...[/bold cyan]")
    console.print(f"Mode: [bold {'yellow' if DRY_RUN else 'red'}]{'DRY RUN' if DRY_RUN else 'DELETION ENABLED'}[/]")
    console.print(f"Looking for items older than {OLDER_THAN_DAYS} days.")

    show_disk_usage_report(console)

    conn = get_db_connection()
    cutoff_date = datetime.now() - timedelta(days=OLDER_THAN_DAYS)
    
    sessions_to_delete = []
    pdfs_to_delete = []
    
    # --- 1. Identify Sessions to Delete ---
    all_sessions = conn.execute('SELECT id, created_at, original_filename, persist FROM sessions').fetchall()
    
    with console.status("[cyan]Analyzing sessions...[/cyan]") as status:
        for session in all_sessions:
            session_id = session['id']
            reason = ""
            
            created_at = datetime.fromisoformat(session['created_at'])
            
            if created_at > cutoff_date:
                reason = REASON_RECENT
            elif session['persist'] == 1:
                reason = REASON_PERSISTED
            elif session['original_filename'] and ('.json' in session['original_filename'].lower() or 'neetprep' in session['original_filename'].lower()):
                reason = REASON_NEETPREP
            elif is_classified_session(conn, session_id):
                reason = REASON_CLASSIFIED

            if not reason:
                sessions_to_delete.append(session)
            status.update(f"[cyan]Analyzed {len(all_sessions)} sessions. Found {len(sessions_to_delete)} candidates for deletion.[/cyan]")

    # --- 2. Identify Generated PDFs to Delete ---
    all_pdfs = conn.execute('SELECT id, session_id, filename, created_at, persist, source_filename, notes FROM generated_pdfs').fetchall()

    with console.status("[cyan]Analyzing generated PDFs...[/cyan]") as status:
        for pdf in all_pdfs:
            reason = ""
            
            created_at = datetime.fromisoformat(pdf['created_at'])
            
            if created_at > cutoff_date:
                reason = REASON_RECENT
            elif pdf['persist'] == 1:
                reason = REASON_PERSISTED
            elif pdf['source_filename'] and ('.json' in pdf['source_filename'].lower() or 'neetprep' in pdf['source_filename'].lower()):
                reason = REASON_NEETPREP
            elif pdf['notes'] and 'json upload' in pdf['notes'].lower():
                reason = REASON_NEETPREP
            elif is_classified_session(conn, pdf['session_id']):
                reason = REASON_CLASSIFIED

            if not reason:
                pdfs_to_delete.append(pdf)
            status.update(f"[cyan]Analyzed {len(all_pdfs)} PDFs. Found {len(pdfs_to_delete)} candidates for deletion.[/cyan]")

    # --- 3. Display Findings ---
    table = Table(title="Items Marked for Deletion", show_header=True, header_style="bold magenta")
    table.add_column("Type", style="dim", min_width=10)
    table.add_column("ID / Filename", style="cyan", min_width=30)
    table.add_column("Created At", style="green", min_width=20)
    table.add_column("Age (Days)", style="yellow", min_width=10)
    table.add_column("Details", min_width=30)

    if not sessions_to_delete and not pdfs_to_delete:
        console.print("\n[bold green]No items found to delete. Everything is up to date.[/bold green]")
        conn.close()
        return

    for session in sessions_to_delete:
        age = (datetime.now() - datetime.fromisoformat(session['created_at'])).days
        table.add_row("Session", session['id'], session['created_at'], str(age), session['original_filename'])

    for pdf in pdfs_to_delete:
        age = (datetime.now() - datetime.fromisoformat(pdf['created_at'])).days
        table.add_row("Generated PDF", pdf['filename'], pdf['created_at'], str(age), f"Source: {pdf['source_filename']}")
        
    console.print(table)

    if DRY_RUN:
        console.print("\n[bold yellow]This was a DRY RUN. No files or database records were deleted.[/bold yellow]")
        console.print("To run the deletion, change the [code]DRY_RUN[/code] flag to [code]False[/code] in the script.")
    else:
        # --- 4. Perform Deletion ---
        console.print("\n[bold red]PERFORMING DELETION...[/bold red]")
        
        # Delete Sessions and associated files
        for session in sessions_to_delete:
            session_id = session['id']
            console.print(f"Deleting session [cyan]{session_id}[/cyan]...")
            images_to_delete = conn.execute('SELECT filename, processed_filename FROM images WHERE session_id = ?', (session_id,)).fetchall()
            for img in images_to_delete:
                if img['filename']:
                    try:
                        f_path = os.path.join(UPLOAD_FOLDER, img['filename'])
                        os.remove(f_path)
                        console.print(f"  - Deleted upload: [dim]{f_path}[/dim]")
                    except OSError as e:
                        console.print(f"  - [red]Error deleting {f_path}: {e}[/red]")
                if img['processed_filename']:
                    try:
                        f_path = os.path.join(PROCESSED_FOLDER, img['processed_filename'])
                        os.remove(f_path)
                        console.print(f"  - Deleted processed: [dim]{f_path}[/dim]")
                    except OSError as e:
                        console.print(f"  - [red]Error deleting {f_path}: {e}[/red]")
            
            conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
            conn.execute('DELETE FROM images WHERE session_id = ?', (session_id,))
            conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
            console.print(f"  - Deleted DB records for session {session_id}")

        # Delete Generated PDFs and their files
        for pdf in pdfs_to_delete:
            pdf_id, pdf_filename = pdf['id'], pdf['filename']
            console.print(f"Deleting generated PDF [cyan]{pdf_filename}[/cyan]...")
            try:
                f_path = os.path.join(OUTPUT_FOLDER, pdf_filename)
                os.remove(f_path)
                console.print(f"  - Deleted file: [dim]{f_path}[/dim]")
            except OSError as e:
                console.print(f"  - [red]Error deleting {f_path}: {e}[/red]")
            
            conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))
            console.print(f"  - Deleted DB record for PDF {pdf_id}")

        conn.commit()
        console.print("\n[bold green]Deletion complete.[/bold green]")

    conn.close()

if __name__ == "__main__":
    main()
