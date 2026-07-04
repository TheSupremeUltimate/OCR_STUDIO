import os
from pypdf import PdfReader, PdfWriter
from tqdm import tqdm

input_pdf = r"D:\OCR_PROJECTS\iching_cn_192.pdf"
output_dir = r"D:\OCR_PROJECTS\iching_cn_split"

os.makedirs(output_dir, exist_ok=True)

print(f"Reading {input_pdf}...")
reader = PdfReader(input_pdf)
total_pages = len(reader.pages)

print(f"Splitting {total_pages} pages into {output_dir}...")
for i in tqdm(range(total_pages)):
    writer = PdfWriter()
    writer.add_page(reader.pages[i])
    
    # Format the filename with zero padding so they sort correctly
    output_filename = os.path.join(output_dir, f"page_{i+1:03d}.pdf")
    with open(output_filename, "wb") as f:
        writer.write(f)

print("Split complete!")
