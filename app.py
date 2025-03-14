import os
import pandas as pd
from flask import Flask, request, send_file, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
import requests
from bs4 import BeautifulSoup
import json
import time
import logging
import uuid

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=10)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

db_path = "price_cache.db"
upload_folder = "uploads"
result_folder = "results"
os.makedirs(upload_folder, exist_ok=True)
os.makedirs(result_folder, exist_ok=True)

# Создание базы данных для кеша
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

# Функция парсинга цен с сайтов
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
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                price_element = soup.find("span", class_="price-value")
                if price_element:
                    price = float(price_element.text.replace(" ", "").replace("₽", ""))
                    results.append({"store": store, "price": price, "url": url})
        except Exception as e:
            logging.error(f"Ошибка при парсинге {store}: {e}")
        time.sleep(0.3)
    return results if results else None

# Функция проверки и обновления кеша
def check_and_update_price(article):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT prices, last_updated FROM prices_cache WHERE article = ?", (article,))
        row = cursor.fetchone()
        if row and pd.Timestamp(row[1]) >= pd.Timestamp.now() - pd.Timedelta(days=7):
            return json.loads(row[0])
        prices = get_price_from_sites(article)
        if prices:
            cursor.execute("REPLACE INTO prices_cache (article, prices, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)",
                           (article, json.dumps(prices)))
            conn.commit()
        return prices

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    if file:
        file_id = str(uuid.uuid4())
        file_path = os.path.join(upload_folder, f"{file_id}.xlsx")
        file.save(file_path)
        return process_excel(file_path, file_id)
    return jsonify({"status": "error", "message": "No file uploaded"}), 400

# Обработка Excel-файла
def process_excel(file_path, file_id):
    df = pd.read_excel(file_path)
    if 'Каталожный номер' not in df.columns or 'Цена заказчика' not in df.columns:
        return jsonify({"status": "error", "message": "Ошибка: В файле нет необходимых колонок"}), 400

    df['Найденные цены'] = None
    df['Разница с ценой заказчика'] = None
    df['Магазины'] = None
    df['Ссылки'] = None

    futures = {executor.submit(check_and_update_price, row['Каталожный номер']): idx for idx, (index, row) in enumerate(df.iterrows())}

    total_difference = 0
    total_customer_price = df['Цена заказчика'].sum()

    for future in as_completed(futures):
        index = futures[future]
        price_data = future.result()
        if price_data:
            prices = ", ".join([f"{p['store']}: {p['price']} ₽" for p in price_data])
            urls = ", ".join([p['url'] for p in price_data])
            min_price = min([p['price'] for p in price_data])

            df.at[index, 'Найденные цены'] = prices
            difference = df.at[index, 'Цена заказчика'] - min_price
            total_difference += difference
            df.at[index, 'Разница с ценой заказчика'] = difference
            df.at[index, 'Магазины'] = ", ".join([p['store'] for p in price_data])
            df.at[index, 'Ссылки'] = urls

    percent_difference = (total_difference / total_customer_price) * 100 if total_customer_price else 0
    result_status = "Выгодно" if total_difference > 0 else "Не выгодно"
    result_row = pd.DataFrame({
        'Каталожный номер': ['ИТОГО'],
        'Цена заказчика': [total_customer_price],
        'Найденные цены': [result_status],
        'Разница с ценой заказчика': [f"{total_difference:.2f} ₽ ({percent_difference:.2f}%)"],
        'Магазины': [''],
        'Ссылки': ['']
    })

    df = pd.concat([result_row, df], ignore_index=True)

    output_file = os.path.join(result_folder, f"{file_id}_result.xlsx")
    df.to_excel(output_file, index=False)
    return send_file(output_file, as_attachment=True)

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
