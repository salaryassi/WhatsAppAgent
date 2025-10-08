FROM python:3.11-slim

WORKDIR /app

# Copy only requirements first for caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code
COPY ./app ./app

# Expose port for FastAPI
EXPOSE 5050

# Set environment variable for Pyrogram session folder
ENV PYROGRAM_SESSION_DIR=/app/sessions

# Start the app with Gunicorn + Uvicorn
CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:5050"]
