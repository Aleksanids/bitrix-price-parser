import os
import pandas as pd
from flask import Flask, request, send_file, jsonify, render_template
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
import requests
from bs4 import BeautifulSoup
import json
import time
import logging
import uuid
import gc

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=5)

# Пути для хранения файлов
db_path = "price_cache.db"
upload_folder = "uploads"
result_folder = "results"
os.makedirs(upload_folder, exist_ok=True)
os.makedirs(result_folder, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def init_db():
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS prices_cache (
                article TEXT PRIMARY KEY,
                prices TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

@app.route('/', methods=['GET'])
def home():
    return render_template("upload.html")

def get_price_from_sites(article):
    sites = {
        "Exist.ru": f"https://exist.ru/Parts?article={article}",
        "ZZap.ru": f"https://www.zzap.ru/search/?query={article}",
        "Auto.ru": f"https://auto.ru/parts/{article}/"
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    results = []
    
    for store, url in sites.items():
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                price_element = soup.find("span", class_="price-value")
                if price_element:
                    price_text = price_element.get_text(strip=True).replace(" ", "").replace("₽", "").replace(",", ".")
                    try:
                        price = float(price_text)
                        results.append({"store": store, "price": price, "url": url})
                    except ValueError:
                        logging.warning(f"Не удалось преобразовать цену на {store}")
        except Exception as e:
            logging.error(f"Ошибка при парсинге {store}: {e}")
        time.sleep(0.3)
    
    return results if results else None

def check_and_update_price(article):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT prices, last_updated FROM prices_cache WHERE article = ?", (article,))
        row = cursor.fetchone()
        
        if row:
            try:
                last_updated = pd.Timestamp(row[1])
                if last_updated >= pd.Timestamp.now() - pd.Timedelta(days=7):
                    return json.loads(row[0])
            except Exception:
                pass

        prices = get_price_from_sites(article)
        if prices:
            cursor.execute("REPLACE INTO prices_cache (article, prices, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)",
                           (article, json.dumps(prices)))
            conn.commit()
        return prices

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    if not file:
        return jsonify({"status": "error", "message": "Файл не загружен"}), 400

    file_id = str(uuid.uuid4())
    file_path = os.path.join(upload_folder, f"{file_id}.xlsx")
    file.save(file_path)

    if os.path.getsize(file_path) > 10 * 1024 * 1024:
        os.remove(file_path)
        return jsonify({"status": "error", "message": "Файл слишком большой. Максимальный размер 10 МБ."}), 400

    return process_excel(file_path, file_id)

def process_excel(file_path, file_id):
    try:
        df = pd.read_excel(file_path, usecols=["Каталожный номер", "Цена заказчика"])
    except ValueError:
        return jsonify({"status": "error", "message": "Файл не содержит нужных колонок."}), 400

    if df.empty:
        return jsonify({"status": "error", "message": "Файл пуст."}), 400

    df['Найденные цены'] = None
    df['Разница с ценой заказчика'] = None
    df['Магазины'] = None
    df['Ссылки'] = None

    futures = {executor.submit(check_and_update_price, row['Каталожный номер']): idx for idx, row in df.iterrows() if pd.notna(row['Каталожный номер'])}

    for future in as_completed(futures):
        index = futures[future]
        price_data = future.result()
        if price_data:
            min_price = min(p['price'] for p in price_data)
            df.at[index, 'Найденные цены'] = min_price
            df.at[index, 'Разница с ценой заказчика'] = df.at[index, 'Цена заказчика'] - min_price
            df.at[index, 'Магазины'] = ", ".join(p['store'] for p in price_data)
            df.at[index, 'Ссылки'] = ", ".join(p['url'] for p in price_data)

    output_file = os.path.join(result_folder, f"{file_id}_result.xlsx")
    df.to_excel(output_file, index=False)

    return send_file(output_file, as_attachment=True, download_name=f"{file_id}_result.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == '__main__':
    init_db()
    app.run(host="0.0.0.0", port=10000)
