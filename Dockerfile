FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "supplier_radar.main"]
