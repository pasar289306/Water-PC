FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AQUA_DATA_DIR=/data
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
RUN useradd -m aqua && mkdir /data && chown aqua /data
USER aqua
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
# AQUA_DEMO=0 برای استقرار واقعی (بدون مجتمع نمونه)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
