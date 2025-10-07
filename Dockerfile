FROM python:3.11-slim

WORKDIR /app

# Copy requirements first to leverage caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY ./app ./app

# Expose port
EXPOSE 5050

# Start the app with Gunicorn + Uvicorn
CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:5050"]
