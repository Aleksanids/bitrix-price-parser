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
import gc  # Модуль для очистки памяти

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=5)  # Ограничиваем количество потоков

# Пути для хранения файлов
db_path = "price_cache.db"
upload_folder = "uploads"
result_folder = "results"
os.makedirs(upload_folder, exist_ok=True)
os.makedirs(result_folder, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Создаём базу данных для кеша цен
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

# Главная страница с загрузкой файла в Битрикс24
@app.route('/', methods=['GET'])
def home():
    return render_template("upload.html")

# Функция парсинга цен
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
            response = requests.get(url, headers=headers, timeout=5)  # Ограничиваем ожидание ответа
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

# Проверка кеша и обновление цен
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

# Обработчик загрузки файлов
@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    if not file:
        return jsonify({"status": "error", "message": "Файл не загружен"}), 400

    file_id = str(uuid.uuid4())
    file_path = os.path.join(upload_folder, f"{file_id}.xlsx")
    file.save(file_path)

    # Ограничение размера файла (максимум 10 МБ)
    MAX_FILE_SIZE_MB = 10
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        return jsonify({"status": "error", "message": "Ошибка: Файл слишком большой. Максимальный размер 10 МБ."}), 400

    return process_excel(file_path, file_id)

# Обработка Excel-файла
def process_excel(file_path, file_id):
    df = pd.read_excel(file_path, usecols=["Каталожный номер", "Цена заказчика"])  # Загружаем только нужные колонки

    if df.empty:
        return jsonify({"status": "error", "message": "Ошибка: Файл пуст или не содержит нужных колонок."}), 400

    df['Найденные цены'] = None
    df['Разница с ценой заказчика'] = None
    df['Магазины'] = None
    df['Ссылки'] = None

    futures = {executor.submit(check_and_update_price, row['Каталожный номер']): idx for idx, row in df.iterrows()}

    for future in as_completed(futures):
        index = futures[future]
        price_data = future.result()
        if price_data:
            min_price = min([p['price'] for p in price_data])
            df.at[index, 'Найденные цены'] = min_price
            df.at[index, 'Разница с ценой заказчика'] = df.at[index, 'Цена заказчика'] - min_price
            df.at[index, 'Магазины'] = ", ".join([p['store'] for p in price_data])
            df.at[index, 'Ссылки'] = ", ".join([p['url'] for p in price_data])

    output_file = os.path.join(result_folder, f"{file_id}_result.xlsx")
    with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)

    if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
        return jsonify({"status": "error", "message": "Ошибка: Файл пустой"}), 500

    del df  # Удаляем DataFrame из памяти
    gc.collect()  # Принудительно очищаем память

    return send_file(output_file, as_attachment=True, download_name=f"{file_id}_result.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == '__main__':
    init_db()
    app.run(host="0.0.0.0", port=10000)
