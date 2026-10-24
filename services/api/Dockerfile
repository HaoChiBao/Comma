FROM python:3.12-slim

WORKDIR /app
COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/api /app
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["python", "-m", "app.entrypoint"]
