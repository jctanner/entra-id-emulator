FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code is NOT copied -- mounted at runtime
EXPOSE 8080
CMD ["python", "run.py"]
