import os

split_dir = r"D:\OCR_PROJECTS\iching_cn_split"
output_file = r"D:\OCR_PROJECTS\iching_cn_192_FULL.md"
total_pages = 192

with open(output_file, "w", encoding="utf-8") as outfile:
    for i in range(1, total_pages + 1):
        md_file = os.path.join(split_dir, f"page_{i:03d}.md")
        if os.path.exists(md_file):
            with open(md_file, "r", encoding="utf-8") as infile:
                content = infile.read()
                # Optional: Add a markdown comment to indicate the page number
                outfile.write(f"<!-- PAGE {i:03d} -->\n")
                outfile.write(content)
                outfile.write("\n\n")
        else:
            outfile.write(f"<!-- PAGE {i:03d} FAILED OR EMPTY -->\n\n")

print(f"Merged {total_pages} pages into {output_file}")
