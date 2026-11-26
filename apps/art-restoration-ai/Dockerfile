FROM python:3.11-slim
WORKDIR /srv
COPY pyproject.toml README.md ./
COPY app ./app
COPY restoration ./restoration
COPY resource_manager ./resource_manager
RUN pip install --no-cache-dir . && useradd --create-home appuser && mkdir -p /srv/data /srv/work && chown -R appuser:appuser /srv
USER appuser
ENV DATA_DIR=/srv/data WORK_DIR=/srv/work
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
