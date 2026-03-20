import os
import hashlib
import wave
import shutil
import uuid
import cv2
import numpy as np
import zlib
from datetime import datetime
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet, InvalidToken
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import base64

# --- Enterprise Security: Rate Limiting ---
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

app = Flask(__name__)
# Read from .env explicitly for WSGI architectures (e.g. Render)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_local_vault_key')

if os.environ.get('VERCEL') == '1':
    db_path = os.path.join('/tmp', 'steg_vault.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URI', f'sqlite:///{db_path}')
    TEMP_VAULT = os.path.join('/tmp', 'temp_vault')
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URI', 'sqlite:///steg_vault.db')
    TEMP_VAULT = os.path.join(app.root_path, 'static', 'temp_vault')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

os.makedirs(TEMP_VAULT, exist_ok=True)

# Rate Limiter configured per IP
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["500 per day", "100 per hour"]
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class VaultLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(10), nullable=False) 
    original_filename = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(20), nullable=False, default="IMAGE")
    payload_size_bytes = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_fernet_key(password: str) -> bytes:
    hashed_password = hashlib.sha256(password.encode()).digest()
    return base64.urlsafe_b64encode(hashed_password)

EOF_BYTES = b"||EOF||"
VIDEO_MAGIC_HEADER_BYTES = b"||STEG_VAULT_START||"

# --- SECURITY UTILITY: Magic Bytes Signature ---
def verify_file_signature(file_path):
    """
    Enterprise Security Hardening: Validates absolute leading file hex headers (Magic Numbers) 
    to completely prevent execution of hostile payloads or reverse shells bypassing the file extension filters.
    """
    if not os.path.exists(file_path):
        return False
        
    with open(file_path, "rb") as f:
        header = f.read(12)
        
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return True
    # JPG: FF D8 FF
    if header.startswith(b'\xff\xd8\xff'):
        return True
    # WAV: RIFF .... WAVE
    if header.startswith(b'RIFF') and header[8:12] == b'WAVE':
        return True
    # MP4/AVI wrapper fallback (can be nested, but realistically checks ftyp/AVI)
    if b'ftyp' in header or (header.startswith(b'RIFF') and header[8:12] == b'AVI '):
        return True
        
    return False

def secure_shred(file_path, passes=3):
    if not os.path.exists(file_path): return
    try:
        length = os.path.getsize(file_path)
        with open(file_path, "ba+") as f:
            for _ in range(passes):
                f.seek(0)
                f.write(os.urandom(length))
        os.remove(file_path)
    except:
        try: os.remove(file_path)
        except: pass

def bytes_to_binary(data_bytes):
    return ''.join(format(b, '08b') for b in data_bytes)

def binary_to_bytes(binary_string):
    b = bytearray()
    for i in range(0, len(binary_string), 8):
        byte = binary_string[i:i+8]
        if len(byte) == 8:
            b.append(int(byte, 2))
    return bytes(b)

def extract_payload_from_binary(binary_string):
    full_bytes = binary_to_bytes(binary_string)
    if EOF_BYTES in full_bytes:
        return full_bytes.split(EOF_BYTES)[0]
    return None

def build_payload(secret_text, aes_key):
    """
    Payload Optimization (zlib) + Encryption Matrix
    The AES block is immediately deflated via Zlib mathematically packing repeating byte blocks.
    Reduces the final LSB requirements ensuring more data fits inside smaller container media.
    """
    f = Fernet(get_fernet_key(aes_key))
    
    # Encrypt raw text
    encrypted_bytes = f.encrypt(secret_text.encode('utf-8'))
    
    # MODULE 1: zlib output minimizes byte profile
    compressed_bytes = zlib.compress(encrypted_bytes)
    
    # Hash Integrity Prefix (64 bytes hex representation)
    hash_bytes = hashlib.sha256(compressed_bytes).hexdigest().encode('utf-8')
    
    # Combine block segments correctly sequenced
    combined_bytes = hash_bytes + compressed_bytes + EOF_BYTES
    
    return bytes_to_binary(combined_bytes), combined_bytes


# --- IMAGE PROCESSING ---
def embed_image(image_path, binary_data, output_path):
    img = Image.open(image_path)
    if img.mode != 'RGB': img = img.convert('RGB')
    pixels = img.load()
    width, height = img.size
    idx, dlen = 0, len(binary_data)
    for y in range(height):
        for x in range(width):
            pixel = list(pixels[x, y])
            for i in range(3):
                if idx < dlen:
                    pixel[i] = pixel[i] & ~1 | int(binary_data[idx])
                    idx += 1
            pixels[x, y] = tuple(pixel)
            if idx >= dlen: break
        if idx >= dlen: break
    img.save(output_path, format="PNG")

def extract_image(image_path):
    img = Image.open(image_path)
    if img.mode != 'RGB': img = img.convert('RGB')
    binary_data = ""
    for r, g, b in list(img.getdata()):
        binary_data += str(r & 1)
        binary_data += str(g & 1)
        binary_data += str(b & 1)
    return extract_payload_from_binary(binary_data)

# --- AUDIO PROCESSING ---
def embed_audio(audio_path, binary_data, output_path):
    with wave.open(audio_path, "rb") as audio:
        frames = bytearray(audio.readframes(audio.getnframes()))
        params = audio.getparams()
    if len(binary_data) > len(frames):
        raise ValueError("Audio file too small.")
    for i in range(len(binary_data)):
        frames[i] = (frames[i] & 254) | int(binary_data[i])
    with wave.open(output_path, "wb") as fd:
        fd.setparams(params)
        fd.writeframes(frames)

def extract_audio(audio_path):
    with wave.open(audio_path, "rb") as audio:
        frames = bytearray(audio.readframes(audio.getnframes()))
    binary_data = ""
    for b in frames:
        binary_data += str(b & 1)
    return extract_payload_from_binary(binary_data)

# --- VIDEO PROCESSING ---
def embed_video(video_path, payload_bytes, output_path):
    shutil.copyfile(video_path, output_path)
    with open(output_path, "ab") as f:
        f.write(VIDEO_MAGIC_HEADER_BYTES + payload_bytes)

def extract_video(video_path):
    with open(video_path, "rb") as f:
        content = f.read()
    if VIDEO_MAGIC_HEADER_BYTES in content:
        target = content.split(VIDEO_MAGIC_HEADER_BYTES)[-1]
        if EOF_BYTES in target:
            return target.split(EOF_BYTES)[0]
    return None

def trigger_steag(file_path, original_filename, media_type, secret_text, aes_key):
    """Shared Embedded Logic Core"""
    binary_data, raw_bytes = build_payload(secret_text, aes_key)
    output_filename = f"enc_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{original_filename.split('.')[0]}"
    
    if media_type == 'AUDIO':
        output_filename += ".wav"
        output_path = os.path.join(TEMP_VAULT, output_filename)
        embed_audio(file_path, binary_data, output_path)
    elif media_type == 'VIDEO':
        ext = os.path.splitext(original_filename)[1]
        if not ext: ext = ".mp4"
        output_filename += ext
        output_path = os.path.join(TEMP_VAULT, output_filename)
        embed_video(file_path, raw_bytes, output_path)
    else: 
        output_filename += ".png"
        output_path = os.path.join(TEMP_VAULT, output_filename)
        embed_image(file_path, binary_data, output_path)
    
    secure_shred(file_path)
    db.session.add(VaultLog(
        user_id=current_user.id, action='ENCODE', 
        original_filename=original_filename, media_type=media_type, 
        payload_size_bytes=len(raw_bytes)
    ))
    db.session.commit()
    return output_filename

with app.app_context():
    db.create_all()

# --- FLASK ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password_hash, request.form.get('password')):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash("Invalid protocol syntax or passkey.", "danger")
    return render_template('auth.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if User.query.filter((User.username == request.form.get('username')) | (User.email == request.form.get('email'))).first():
            flash("Alias or Email already registered within the network.", "danger")
            return redirect(url_for('register'))
        new_user = User(
            username=request.form.get('username'), 
            email=request.form.get('email'),
            password_hash=generate_password_hash(request.form.get('password'))
        )
        db.session.add(new_user)
        db.session.commit()
        flash("Agent successfully initialized. Proceed to terminal access.", "success")
        return redirect(url_for('login'))
    return render_template('auth.html', is_register=True)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        new_username = request.form.get('username')
        new_email = request.form.get('email')
        
        if new_username and new_username != current_user.username:
            if User.query.filter_by(username=new_username).first():
                flash("Alias already assigned.", "danger")
                return redirect(url_for('profile'))
            current_user.username = new_username
            
        if new_email and new_email != current_user.email:
            if User.query.filter_by(email=new_email).first():
                flash("Email already assigned.", "danger")
                return redirect(url_for('profile'))
            current_user.email = new_email
            
        db.session.commit()
        flash("Profile matrix successfully updated.", "success")
        return redirect(url_for('profile'))
        
    return render_template('profile.html')

@app.route('/dashboard')
@login_required
def dashboard():
    logs = VaultLog.query.filter_by(user_id=current_user.id).order_by(VaultLog.timestamp.desc()).all()
    total_encoded = sum(1 for log in logs if log.action == 'ENCODE')
    total_bytes = sum(log.payload_size_bytes for log in logs if log.action == 'ENCODE')
    last_activity = logs[0].timestamp if logs else None
    return render_template('dashboard.html', logs=logs, total_encoded=total_encoded, total_bytes=total_bytes, last_activity=last_activity)

@app.route('/workspace')
@login_required
def workspace():
    return render_template('workspace.html')

@app.route('/forensics')
@login_required
def forensics():
    return render_template('forensics.html')

@app.route('/api/upload_chunk', methods=['POST'])
@login_required
def upload_chunk():
    try:
        chunk = request.files.get('chunk')
        if not chunk: return jsonify({"error": "Chunk delivery missing."}), 400
            
        file_id = request.form.get('file_id')
        chunk_index = int(request.form.get('chunk_index', 0))
        total_chunks = int(request.form.get('total_chunks', 1))
        
        media_type = request.form.get('media_type', 'IMAGE').upper()
        secret_text = request.form.get('secret_text', '')
        aes_key = request.form.get('aes_key', '')
        original_filename = secure_filename(request.form.get('original_filename', 'chunk_stream.dat'))
        
        temp_path = os.path.join(TEMP_VAULT, f"chunkstream_{file_id}.part")
        
        with open(temp_path, "ab") as f: f.write(chunk.read())
            
        if chunk_index == total_chunks - 1:
            if not secret_text or not aes_key:
                secure_shred(temp_path)
                return jsonify({"error": "Cryptographic payload markers missed."}), 400
            
            # Signature Validation ensuring malicious shell files aren't masquerading
            if not verify_file_signature(temp_path):
                secure_shred(temp_path)
                return jsonify({"error": "Integrity Check Failed: Invalid Magic Headers."}), 400
            
            output_filename = trigger_steag(temp_path, original_filename, media_type, secret_text, aes_key)
            return jsonify({
                "success": True, 
                "download_url": url_for('download_file', filename=output_filename), 
                "message": f"[{media_type}] Large package assembled correctly."
            })
            
        return jsonify({"success": True})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/encode', methods=['POST'])
@login_required
def api_encode():
    if 'file' not in request.files: return jsonify({"error": "Media package missing"}), 400
    file = request.files['file']
    media_type = request.form.get('media_type', 'image').upper()
    secret_text = request.form.get('secret_text', '')
    aes_key = request.form.get('aes_key', '')
    
    if file.filename == '' or not secret_text or not aes_key: return jsonify({"error": "Missing keys"}), 400
        
    try:
        input_filename = secure_filename(file.filename)
        temp_in = os.path.join(TEMP_VAULT, "temp_enc_" + input_filename)
        file.save(temp_in)
        
        if not verify_file_signature(temp_in):
            secure_shred(temp_in)
            return jsonify({"error": "Integrity Check Failed: Uploaded file is tampered."}), 400
            
        output_filename = trigger_steag(temp_in, input_filename, media_type, secret_text, aes_key)
        return jsonify({"success": True, "download_url": url_for('download_file', filename=output_filename), "message": f"[{media_type}] Payload locked."})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# MODULE 2: Security Hardening (Limiting excessive unauthorized AES cracking entries)
@app.route('/api/decode', methods=['POST'])
@login_required
@limiter.limit("5 per minute", error_message='{"error": "API Rate Limit Exceeded. Bruteforce Defense Engaged."}')
def api_decode():
    if 'file' not in request.files: return jsonify({"error": "No secure media detected"}), 400
    file = request.files['file']
    media_type = request.form.get('media_type', 'image').upper()
    aes_key = request.form.get('aes_key', '')
    
    if file.filename == '' or not aes_key: return jsonify({"error": "Fields incomplete."}), 400
        
    try:
        raw_bytes = None
        temp_in = os.path.join(TEMP_VAULT, "temp_dec_" + secure_filename(file.filename))
        file.save(temp_in)
        
        if not verify_file_signature(temp_in):
            secure_shred(temp_in)
            return jsonify({"error": "Integrity Check Failed: Invalid target."}), 400
        
        if media_type == 'AUDIO': raw_bytes = extract_audio(temp_in)
        elif media_type == 'VIDEO': raw_bytes = extract_video(temp_in)
        else: raw_bytes = extract_image(temp_in)
            
        secure_shred(temp_in) 
            
        if not raw_bytes: return jsonify({"error": "No payload extracted."}), 400
            
        # Break apart the block (64 char hash string + compressed block)
        hash_prefix = raw_bytes[:64].decode('utf-8', errors='ignore')
        compressed_text = raw_bytes[64:]
        
        if hash_prefix != hashlib.sha256(compressed_text).hexdigest():
            return jsonify({"error": "INTEGRITY FAULT: Hash decoupled."}), 400
            
        # Optimization MODULE 1: Zlib Decompress extracted container bytes
        f = Fernet(get_fernet_key(aes_key))
        try:
            decrypted_block = zlib.decompress(compressed_text)
            decrypted_text = f.decrypt(decrypted_block).decode('utf-8')
        except InvalidToken:
            return jsonify({"error": "DENIED: Invalid AES Key."}), 400
        except Exception as zerr:
            return jsonify({"error": "Zlib extraction corrupt: " + str(zerr)}), 400
            
        db.session.add(VaultLog(user_id=current_user.id, action='DECODE', original_filename=secure_filename(file.filename), media_type=media_type, payload_size_bytes=len(raw_bytes)))
        db.session.commit()
        return jsonify({"success": True, "message": decrypted_text})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_analyze():
    if 'file' not in request.files: return jsonify({"error": "Media required."}), 400
    file = request.files['file']
    filename = secure_filename(file.filename)
    temp_path = os.path.join(TEMP_VAULT, "analyze_" + filename)
    file.save(temp_path)
    
    try:
        ext = os.path.splitext(filename)[1].lower()
        img = None
        
        # Audio Forensic Matrix extraction via Numpy byte chunking
        if ext == '.wav':
            with wave.open(temp_path, 'rb') as wav_file:
                frames = wav_file.readframes(wav_file.getnframes())
                data = np.frombuffer(frames, dtype=np.uint8)
                width = 512
                height = len(data) // width
                if height > 0:
                    data = data[:width * height]
                    img = data.reshape((height, width))
                else:
                    padded = np.zeros(width, dtype=np.uint8)
                    padded[:len(data)] = data
                    img = padded.reshape((1, width))
                    
        # Video Forensic extraction via CV cap
        elif ext in ['.mp4', '.avi']:
            cap = cv2.VideoCapture(temp_path)
            ret, frame = cap.read()
            cap.release()
            if ret: img = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                secure_shred(temp_path)
                return jsonify({"error": "Video frame extraction failed. Corrupt wrapper."}), 400
                
        # Pure Image Forensics fallback
        else:
            img = cv2.imread(temp_path, cv2.IMREAD_GRAYSCALE)
            
        if img is None:
            secure_shred(temp_path)
            return jsonify({"error": "Failed to decode media map for visualization."}), 400

        operator = request.form.get('operator', 'canny').lower()
        if operator == 'canny': edges = cv2.Canny(img, 100, 200)
        elif operator == 'sobel':
            sobelx, sobely = cv2.Sobel(img, cv2.CV_64F, 1, 0, ksize=3), cv2.Sobel(img, cv2.CV_64F, 0, 1, ksize=3)
            edges = np.uint8(np.absolute(cv2.magnitude(sobelx, sobely)))
        elif operator == 'prewitt':
            kx = np.array([[1,1,1],[0,0,0],[-1,-1,-1]])
            ky = np.array([[-1,0,1],[-1,0,1],[-1,0,1]])
            edges = cv2.addWeighted(cv2.filter2D(img, -1, kx), 0.5, cv2.filter2D(img, -1, ky), 0.5, 0)
        else: edges = img
            
        import uuid
        out_filename = f"forensic_{uuid.uuid4().hex[:8]}.png"
        cv2.imwrite(os.path.join(TEMP_VAULT, out_filename), edges)
        
        secure_shred(temp_path)
        return jsonify({"success": True, "download_url": url_for('download_file', filename=out_filename)})
    except Exception as e:
        secure_shred(temp_path)
        return jsonify({"error": str(e)}), 500

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    safe_filename = secure_filename(filename)
    file_path = os.path.join(TEMP_VAULT, safe_filename)
    if os.path.exists(file_path): return send_file(file_path, as_attachment=True)
    flash("Resource wiped.", "danger")
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    with app.app_context(): db.create_all()
    app.run(debug=True)
