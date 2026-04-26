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
COPY requirements.txt .

RUN python3 -m pip install --no-cache-dir -r requirements.txt \
    && (python3 -c "import psutil" 2>/dev/null \
        || python3 -m pip install --no-cache-dir --no-build-isolation psutil || true)

# 설치 검증
RUN echo "Checking packages..." \
    && python3 -c "import json, signal, subprocess, threading, os, time, logging, shutil, tempfile, uuid; from datetime import datetime, timedelta; print('stdlib: OK')" \
    && python3 -c "import pytz; print('pytz: OK')" \
    && python3 -c "import flask; print('flask: OK')" \
    && python3 -c "import psycopg2; print('psycopg2 (PostgreSQL): OK')" \
    && python3 -c "import pymssql; print('pymssql (MSSQL): OK')" \
    && (python3 -c "import pyodbc; print('pyodbc (MSSQL): OK')" || echo "pyodbc: optional skip") \
    && (python3 -c "import psutil; print('psutil: OK')" || echo "psutil: optional") \
    && python3 -c "from selenium import webdriver; from selenium.webdriver import ActionChains; from selenium.webdriver.common.by import By; from selenium.webdriver.common.keys import Keys; from selenium.webdriver.support import expected_conditions as EC; from selenium.webdriver.support.ui import WebDriverWait; from selenium.webdriver.common.desired_capabilities import DesiredCapabilities; print('selenium (template_crawl.py 호환): OK')" \
    && (python3 -c "from seleniumwire import webdriver as w; print('selenium-wire: OK')" || echo "selenium-wire: optional skip") \
    && python3 -c "from bs4 import BeautifulSoup; BeautifulSoup('<p>t</p>','lxml'); BeautifulSoup('<p>t</p>','html.parser'); BeautifulSoup('<p>t</p>','html5lib'); print('beautifulsoup4+lxml+html5lib: OK')" \
    && python3 -c "import lxml.etree; import lxml.html; print('lxml: OK')" \
    && python3 -c "from PIL import Image; print('Pillow: OK')" \
    && echo "Package check completed"

