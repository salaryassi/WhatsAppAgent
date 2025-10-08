import os

source_dir = "./app"
output_file = "all_python_code.txt"

all_code = []

for filename in os.listdir(source_dir):
    if filename.endswith(".py"):
        path = os.path.join(source_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            all_code.append(f"# File: {filename}\n{content}\n\n")

with open(output_file, "w", encoding="utf-8") as f:
    f.write("\n".join(all_code))

print(f"All .py files from {source_dir} written to {output_file}")
