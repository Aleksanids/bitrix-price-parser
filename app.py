import os
import pandas as pd
from flask import Flask, request, send_file, jsonify, render_template
import uuid

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
RESULT_FOLDER = "results"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def home():
    return render_template("index.html")

@app.route('/upload.html')
def upload_page():
    return render_template("upload.html")

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Файл не загружен"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "Имя файла не указано"}), 400
    
    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Разрешены только файлы .xlsx"}), 400

    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.xlsx")
    file.save(file_path)

    return process_excel(file_path, file_id)

def process_excel(file_path, file_id):
    try:
        df = pd.read_excel(file_path)
        if df.empty:
            return jsonify({"status": "error", "message": "Файл пуст"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка обработки файла: {str(e)}"}), 400

    output_file = os.path.join(RESULT_FOLDER, f"{file_id}_result.xlsx")
    df.to_excel(output_file, index=False)
    
    return jsonify({"status": "success", "download_url": f"/download/{file_id}"})

@app.route('/download/<file_id>')
def download_file(file_id):
    output_file = os.path.join(RESULT_FOLDER, f"{file_id}_result.xlsx")
    if os.path.exists(output_file):
        return send_file(output_file, as_attachment=True, download_name=f"{file_id}_result.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return jsonify({"status": "error", "message": "Файл не найден"}), 404

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
