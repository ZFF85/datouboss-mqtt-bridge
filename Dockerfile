FROM python:3.15-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bridge.py LICENSE ./
COPY vendor/ vendor/
ENV PYTHONUNBUFFERED=1
CMD ["python", "bridge.py"]
