FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home emulator \
    && mkdir /data && chown emulator /data
COPY entra_mock ./entra_mock
COPY run.py config.yaml ./
ENV ENTRA_MOCK_DB=/data/entra_mock.db
USER emulator
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/')"
CMD ["flask", "--app", "run:app", "run", "--host=0.0.0.0", "--port=8080", "--no-debugger", "--no-reload"]
