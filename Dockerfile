# flask_app.py + template_crawl.py 실행용 
# 베이스: selenium/standalone-chrome (Ubuntu) — RUN 기본 셸은 dash 이므로 bash 전용 &>/glob 미사용
# FROM selenium/standalone-chrome:145.0.7632.116-chromedriver-145.0.7632.117-20260222
FROM selenium/standalone-chrome:latest

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

# Python3 + 빌드/DB용 패키지 한 번에 설치 (aptget 한번만)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-dev \
        build-essential \
        gcc \
        g++ \
        make \
        unixodbc \
        unixodbc-dev \
        freetds-bin \
        freetds-dev \
        libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && python3 --version \
    && python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /app

