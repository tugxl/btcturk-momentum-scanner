FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py config.example.yaml positions.example.json ./
RUN useradd --create-home scanner && mkdir /data && chown scanner:scanner /data
USER scanner
ENV PYTHONUNBUFFERED=1 SCANNER_DATA_DIR=/data
CMD ["python", "main.py", "--watch"]
