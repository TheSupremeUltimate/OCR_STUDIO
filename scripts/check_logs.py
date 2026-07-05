import os
import sys
import glob
from pathlib import Path

def tail_file(filepath, lines=50):
    """Returns the last `lines` from the given file."""
    try:
        with open(filepath, 'rb') as f:
            # We use a relatively small block size to step backwards
            block_size = 1024
            f.seek(0, 2)
            file_size = f.tell()
            blocks = []
            
            # If the file is smaller than block_size, just read it all
            if file_size < block_size:
                f.seek(0)
                return f.read().decode('utf-8', errors='ignore').splitlines()[-lines:]
                
            bytes_to_read = min(block_size, file_size)
            f.seek(-bytes_to_read, 2)
            
            # Read blocks from the end until we have enough newlines
            while True:
                block = f.read(bytes_to_read)
                blocks.append(block)
                full_text = b''.join(reversed(blocks))
                if full_text.count(b'\n') > lines or f.tell() - bytes_to_read * 2 < 0:
                    break
                # move back 2 blocks (since we just read 1 block forward)
                f.seek(-bytes_to_read * 2, 1)
                
            text = full_text.decode('utf-8', errors='ignore')
            return text.splitlines()[-lines:]
            
    except Exception as e:
        return [f"Error reading {filepath}: {e}"]

def get_latest_lm_studio_log():
    """Finds the most recently modified log file in the LM Studio logs directory."""
    log_dir = Path(r"C:\Users\chist\.lmstudio\server-logs")
    if not log_dir.exists():
        return None
        
    # Search all subdirectories for .log files
    all_logs = list(log_dir.rglob("*.log"))
    if not all_logs:
        return None
        
    # Return the file with the most recent modification time
    latest_log = max(all_logs, key=lambda p: p.stat().st_mtime)
    return latest_log

def main():
    # Force UTF-8 stdout to prevent Windows encoding crashes on Chinese text
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

    print("=" * 80)
    print(" OCR STUDIO & LM STUDIO LOG DIAGNOSTICS ")
    print("=" * 80)
    
    # 1. OCR Studio Logs
    ocr_log_path = Path(__file__).parent.parent / "logs" / "ocr_studio.log"
    print(f"\n[1] OCR STUDIO LOGS")
    print(f"Path: {ocr_log_path}")
    print("-" * 80)
    if ocr_log_path.exists():
        lines = tail_file(ocr_log_path, 30)
        for line in lines:
            print(line)
    else:
        print("-> File not found.")

    print("\n" + "=" * 80)
    
    # 2. LM Studio Logs
    lm_log_path = get_latest_lm_studio_log()
    print(f"\n[2] LM STUDIO LATEST LOGS")
    if lm_log_path:
        print(f"Path: {lm_log_path}")
        print("-" * 80)
        lines = tail_file(lm_log_path, 30)
        for line in lines:
            print(line)
    else:
        print("-> LM Studio logs directory or files not found.")
        
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
