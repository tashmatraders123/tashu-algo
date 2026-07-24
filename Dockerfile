FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# .env is mounted or passed via --env-file at "docker run" time, not baked into the image
CMD ["python", "bot.py"]
