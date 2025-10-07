FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This is the corrected line
COPY ./app ./app

CMD ["python", "-m", "app.main"]