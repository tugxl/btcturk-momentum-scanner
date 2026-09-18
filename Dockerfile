FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py config.example.yaml positions.example.json ./
RUN useradd --create-home scanner && mkdir -p /data/v3 && chown -R scanner:scanner /data
# Railway persistent volumes can be mounted with ownership that does not match
# the image's unprivileged scanner user. Run the worker as the container user
# so the mounted volume remains writable; the scanner uses public market APIs only.
ENV PYTHONUNBUFFERED=1 SCANNER_DATA_DIR=/data SCANNER_V3_DATA_DIR=/data/v3
CMD ["python", "main.py", "--watch"]
