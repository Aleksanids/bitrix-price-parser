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

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=10)

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

# Главная страница с инструкцией для Битрикс24
@app.route('/', methods=['GET', 'POST'])
def home():
    return render_template("index.html")

# Страница загрузки файла
@app.route('/form', methods=['GET'])
def upload_form():
    return render_template("upload.html")

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

# Получение колонок из загруженного файла
@app.route('/columns', methods=['POST'])
def get_columns():
    file = request.files.get('file')
    if not file:
        return jsonify({"status": "error", "message": "Файл не загружен"}), 400

    df = pd.read_excel(file)
    return jsonify({"status": "success", "columns": df.columns.tolist()})

# Обработчик загрузки файлов
@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    column1 = request.form.get('column1')
    column2 = request.form.get('column2')

    if not file or not column1 or not column2:
        return jsonify({"status": "error", "message": "Ошибка: Файл или выбранные колонки не переданы"}), 400

    file_id = str(uuid.uuid4())
    file_path = os.path.join(upload_folder, f"{file_id}.xlsx")
    file.save(file_path)

    return process_excel(file_path, file_id, column1, column2)

# Обработка Excel-файла с выбранными пользователем колонками
def process_excel(file_path, file_id, column1, column2):
    df = pd.read_excel(file_path)

    if column1 not in df.columns or column2 not in df.columns:
        return jsonify({"status": "error", "message": f"Ошибка: В файле нет выбранных колонок. Найдены: {df.columns.tolist()}"}), 400

    df['Найденные цены'] = None
    df['Разница с ценой заказчика'] = None
    df['Магазины'] = None
    df['Ссылки'] = None

    futures = {executor.submit(check_and_update_price, row[column1]): idx for idx, (index, row) in enumerate(df.iterrows())}

    total_difference = 0
    total_customer_price = df[column2].sum()

    for future in as_completed(futures):
        index = futures[future]
        price_data = future.result()
        if price_data:
            prices = ", ".join([f"{p['store']}: {p['price']} ₽" for p in price_data])
            urls = ", ".join([p['url'] for p in price_data])
            min_price = min([p['price'] for p in price_data])

            df.at[index, 'Найденные цены'] = prices
            difference = df.at[index, column2] - min_price
            total_difference += difference
            df.at[index, 'Разница с ценой заказчика'] = difference
            df.at[index, 'Магазины'] = ", ".join([p['store'] for p in price_data])
            df.at[index, 'Ссылки'] = urls

    percent_difference = (total_difference / total_customer_price) * 100 if total_customer_price else 0
    result_status = "Выгодно" if total_difference > 0 else "Не выгодно"
    result_row = pd.DataFrame({
        column1: ['ИТОГО'],
        column2: [total_customer_price],
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
    app.run(host="0.0.0.0", port=10000)
