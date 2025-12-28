
FROM python:3.11-slim

WORKDIR /app

# Install everything in one go
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt yt-dlp

COPY api.py .

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
