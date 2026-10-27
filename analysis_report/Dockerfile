ARG PYTHON_BASE=python:3.12-bookworm
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
RUN pip install --no-cache-dir pandas==2.3.3 numpy==2.2.6 openpyxl==3.1.5 xlrd==2.0.2 plotly==5.24.1
WORKDIR /shared
