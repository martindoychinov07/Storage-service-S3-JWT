from flask import Flask, request, jsonify, send_file
from minio import Minio
from minio.error import S3Error
import os
import jwt
import requests
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__)

minio_client = Minio(
    "minio:9000",
    access_key="minio_access_key",
    secret_key="minio_secret_key",
    secure=False
)

bucket_name = "my-bucket"

def create_bucket_if_not_exists():
    try:
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)
    except S3Error as e:
        print("Error creating bucket: ", e)

def get_keycloak_public_key():
    try:
        url = "http://localhost:8080/realms/file_management/protocol/openid-connect/certs"
        response = requests.get(url)
        response.raise_for_status()
        keycloak_config = response.json()
        public_key = keycloak_config['keys'][0]['x5c'][0]
        print (f"-----BEGIN PUBLIC KEY-----\n{public_key}\n-----END PUBLIC KEY-----")
        return public_key
    except Exception as e:
        print("Error fetching Keycloak public key:", e)
    return None

def verify_jwt(token):
    try:
        if token.startswith("Bearer "):
            token = token[7:]

        public_key = get_keycloak_public_key()
        if not public_key:
            print("Failed to fetch public key")
            return None

        decoded = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience="file-app",
            issuer="http://localhost:8080/realms/file_management"
        )
        return decoded
    except jwt.ExpiredSignatureError:
        print("JWT expired")
    except jwt.InvalidAlgorithmError as e:
        print(f"Invalid Algorithm Error: {e}")
    except jwt.DecodeError as e:
        print(f"JWT Decode Error: {e}")
    except Exception as e:
        print(f"Unexpected error during JWT verification: {e}")
    return None

def save_file_to_minio(file, filename):
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            file.save(tmp_file.name)
            minio_client.fput_object(bucket_name, filename, tmp_file.name)
        os.unlink(tmp_file.name)
    except S3Error as e:
        print(f"Error uploading file to MinIO: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error during file save: {e}")
        return False
    return True

@app.route('/upload', methods=['POST'])
def upload_file():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({"error": "Unauthorized"}), 401

    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    filename = secure_filename(file.filename)

    if not filename:
        return jsonify({"error": "Invalid file name"}), 400

    if save_file_to_minio(file, filename):
        return jsonify({"message": "File uploaded successfully", "filename": filename}), 200
    return jsonify({"error": "Failed to upload file"}), 500

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        response = minio_client.get_object(bucket_name, file_id)
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            for data in response.stream(32 * 1024):
                tmp_file.write(data)
            tmp_file_path = tmp_file.name

        return send_file(tmp_file_path, as_attachment=True, download_name=file_id)
    except S3Error as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"Unexpected error during file download: {e}")
        return jsonify({"error": "Failed to download file"}), 500
    finally:
        if os.path.exists(tmp_file_path):
            os.unlink(tmp_file_path)

@app.route('/update/<file_id>', methods=['PUT'])
def update_file(file_id):
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({"error": "Unauthorized"}), 401

    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    filename = secure_filename(file.filename)

    if not filename:
        return jsonify({"error": "Invalid file name"}), 400

    if save_file_to_minio(file, file_id):
        return jsonify({"message": "File updated successfully", "filename": filename}), 200
    return jsonify({"error": "Failed to update file"}), 500

@app.route('/delete/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        minio_client.remove_object(bucket_name, file_id)
        return jsonify({"message": "File deleted successfully"}), 200
    except S3Error as e:
        print(f"Error during file deletion: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"Unexpected error during file deletion: {e}")
        return jsonify({"error": "Failed to delete file"}), 500

if __name__ == "__main__":
    create_bucket_if_not_exists()
    app.run(debug=True, host="0.0.0.0", port=5000)
