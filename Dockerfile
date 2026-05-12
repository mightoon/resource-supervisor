FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

COPY server_v2.py .
COPY config.json .
COPY servers.json .
COPY users.json .

EXPOSE 5001

CMD ["python", "server_v2.py"]
