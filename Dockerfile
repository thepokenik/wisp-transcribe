FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home transcriber && mkdir /data && chown transcriber:transcriber /data
COPY service.py index.html ./
USER transcriber
ENV HOST=0.0.0.0 DATA_DIR=/data HF_HUB_DISABLE_XET=1
EXPOSE 8000
CMD ["python", "service.py"]
