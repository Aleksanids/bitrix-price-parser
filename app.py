import os
import pandas as pd
from flask import Flask, request, send_file, jsonify, render_template
import uuid

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
RESULT_FOLDER = "results"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)

column_mapping = {}

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

    df = pd.read_excel(file_path)
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

    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "Файл не найден"}), 400

    df = pd.read_excel(file_path)
    article_col = column_mapping.get("article")
    price_col = column_mapping.get("price")

    if not article_col or not price_col:
        return jsonify({"status": "error", "message": "Не выбрано соответствие полей"}), 400

    df["Рыночная цена"] = df[price_col] * 0.9  # Примерное уменьшение цены
    df["Разница в цене"] = df[price_col] - df["Рыночная цена"]
    df["Комментарий"] = df.apply(lambda row: "Цена выше рынка" if row["Разница в цене"] > 0 else "Цена ниже рынка", axis=1)

    output_file = os.path.join(RESULT_FOLDER, f"{file_id}_result.xlsx")
    df.to_excel(output_file, index=False)

    return jsonify({"status": "success", "download_url": f"/download/{file_id}"})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
