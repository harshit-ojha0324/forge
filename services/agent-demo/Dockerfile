FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# Unprivileged, like the gateway image.
USER 10001:10001

CMD ["python", "agent.py", "--loop"]
