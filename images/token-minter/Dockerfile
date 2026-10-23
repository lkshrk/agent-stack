FROM python:3.14-slim
COPY images/token-minter/mint.py /app/mint.py
USER 65532:65532
ENTRYPOINT ["python", "/app/mint.py"]
