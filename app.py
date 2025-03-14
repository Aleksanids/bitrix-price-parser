import os
import re
import time
import uuid
import logging
import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, render_template, send_file
from concurrent.futures import ThreadPoolExecutor, as_completed

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
RESULT_FOLDER = "results"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)

# Глобальная requests-сессия для повторного использования соединений
session = requests.Session()
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Глобальный кэш для цен
price_cache = {}
column_mapping = {}

# Функции парсинга цен (остаются без изменений)
def parse_price_exist(article):
    try:
        url = f"https://www.exist.ru/Price/?pcode={article}"
        response = session.get(url, headers=DEFAULT_HEADERS, timeout=5)
        response.raise_for_status()
        html = response.text.replace('&nbsp;', ' ').replace('\xa0', ' ')
        price_matches = re.findall(r'(\d[\d\s]*)(?:р\.|руб\.)', html)
        price_matches += re.findall(r'(\d[\d\s]*)₽', html)
        prices = [int(re.sub(r'\D', '', p)) for p in price_matches if re.sub(r'\D', '', p)]
        return min(prices) if prices else None
    except Exception as e:
        logging.error(f"Exist parser error: {e}")
        return None

def parse_price_zzap(article):
    try:
        url = f"http://www.zzap.ru/default.aspx?partnumber={article}&currency=1"
        response = session.get(url, headers=DEFAULT_HEADERS, timeout=5)
        response.raise_for_status()
        html = response.text.replace('&nbsp;', ' ').replace('\xa0', ' ')
        price_matches = re.findall(r'(\d[\d\s]*)р\.', html)
        prices = [int(p.replace(' ', '')) for p in price_matches if p.replace(' ', '').isdigit()]
        return min(prices) if prices else None
    except Exception as e:
        logging.error(f"ZZap parser error: {e}")
        return None

def parse_price_major_auto(article):
    try:
        url = f"https://parts.major-auto.ru/SearchNew?value={article}"
        response = session.get(url, headers=DEFAULT_HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        element = soup.find("td", class_=lambda x: x and "price" in x)
        if element:
            return float(re.sub(r"[^\d.]", "", element.get_text()))
        return None
    except Exception as e:
        logging.error(f"Major Auto parser error: {e}")
        return None

# Агрегация цен с кэшированием
def process_article(article_val, original_price):
    if article_val in price_cache:
        logging.info(f"Используем кэш для артикула {article_val}")
        return price_cache[article_val]

    parsers = {
        "Exist": parse_price_exist,
        "ZZap": parse_price_zzap,
        "MajorAuto": parse_price_major_auto
    }

    prices = {source: func(article_val) for source, func in parsers.items()}
    valid_prices = [p for p in prices.values() if p is not None]

    if not valid_prices:
        result = ("Нет данных", "Нет данных", "Нет данных")
    else:
        market_price = min(valid_prices)
        price_diff = float(original_price) - market_price
        comment = "Цена выше рынка" if price_diff > 0 else "Цена ниже рынка" if price_diff < 0 else "Цена на уровне рынка"
        result = (market_price, price_diff, comment)

    price_cache[article_val] = result
    return result

@app.route('/')
def home():
    return render_template("index.html")

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Файл не загружен"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "Имя файла не указано"}), 400

    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.xlsx")
    file.save(file_path)

    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        logging.error(f"Ошибка чтения Excel: {e}")
        return jsonify({"status": "error", "message": "Ошибка чтения Excel"}), 400

    return jsonify({"status": "success", "columns": list(df.columns), "file_id": file_id})

@app.route('/confirm-mapping', methods=['POST'])
def confirm_mapping():
    global column_mapping
    data = request.get_json()
    column_mapping["article"] = data["article_column"]
    column_mapping["price"] = data["price_column"]
    return jsonify({"status": "success", "message": "Соответствие полей установлено!"})

@app.route('/process', methods=['POST'])
def process_file():
    file_id = request.json.get("file_id")
    file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.xlsx")

    if not file_id or not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "Файл не найден"}), 400

    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        logging.error(f"Ошибка чтения файла {file_id}: {e}")
        return jsonify({"status": "error", "message": "Ошибка чтения файла"}), 400

    article_col = column_mapping.get("article")
    price_col = column_mapping.get("price")
    if not article_col or not price_col:
        return jsonify({"status": "error", "message": "Не выбрано соответствие полей"}), 400

    market_prices, price_diffs, comments = [], [], []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_article, str(row[article_col]).strip(), row[price_col]): idx for idx, row in df.iterrows()}

        for future in as_completed(futures):
            idx = futures[future]
            try:
                market_price, price_diff, comment = future.result()
            except Exception as e:
                logging.error(f"Ошибка обработки {idx}: {e}")
                market_price, price_diff, comment = "Нет данных", "Нет данных", "Нет данных"

            market_prices.append(market_price)
            price_diffs.append(price_diff)
            comments.append(comment)

    df["Рыночная цена"], df["Разница в цене"], df["Комментарий"] = market_prices, price_diffs, comments

    output_file = os.path.join(RESULT_FOLDER, f"{file_id}_result.xlsx")
    df.to_excel(output_file, index=False)

    return jsonify({"status": "success", "download_url": f"/download/{file_id}"})

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    file_path = os.path.join(RESULT_FOLDER, f"{file_id}_result.xlsx")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({"status": "error", "message": "Файл не найден"}), 404

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
