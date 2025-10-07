FROM python:3.9-slim

# OS deps for cryptography and pyrogram native deps if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy package directory
COPY ./app ./app

ENV PYTHONUNBUFFERED=1

# default port used by the Flask app
EXPOSE 5000

CMD ["python", "-m", "app.main"]
