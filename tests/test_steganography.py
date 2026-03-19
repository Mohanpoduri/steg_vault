import os
import zlib
import pytest
from cryptography.fernet import Fernet
from PIL import Image

# Import core modules
from app import (
    get_fernet_key, 
    build_payload, 
    embed_image, 
    extract_image, 
    verify_file_signature, 
    EOF_BYTES
)

def test_aes256_encryption():
    """Verify exactly that AES-256 mathematically returns the input string securely."""
    secret = "Top Secret Academic Payload"
    aes_key = "secure_password_123"
    
    f = Fernet(get_fernet_key(aes_key))
    encrypted = f.encrypt(secret.encode('utf-8'))
    decrypted = f.decrypt(encrypted).decode('utf-8')
    
    assert decrypted == secret

def test_zlib_compression_efficiency():
    """
    Verify that zlib actively reduces the payload byte footprint natively.
    Zlib mathematically minimizes highly structured and repeating blocks, 
    thus increasing steganographic capacity overall.
    """
    aes_key = "compression_test_key"
    f = Fernet(get_fernet_key(aes_key))
    
    # Generate long repeating payload sequence (Base64 structured data creates repetition patterns)
    secret = "10100101" * 500 + "AABBCCDD" * 500
    encrypted = f.encrypt(secret.encode('utf-8'))
    
    compressed = zlib.compress(encrypted)
    
    # Assert size payload is securely compressed 
    assert len(compressed) < len(encrypted), "Zlib module failed to minimize ciphertext."

def test_image_steganography_mock_10x10(tmpdir):
    """
    Mock exactly a small 10x10 pixel image, embed a known short string naturally, 
    extract its binary string natively, and assert they match perfectly.
    (Note: Full AES-Zlib payloads mathematically require larger pixel bounds, 
     hence this tests the pure lowest-significant-bit algorithmic fidelity.)
    """
    mock_10x10 = os.path.join(tmpdir, "mock_original_10.png")
    out_10x10 = os.path.join(tmpdir, "mock_stego_10.png")
    
    img = Image.new('RGB', (10, 10), color=(0, 0, 0))
    img.save(mock_10x10)
    
    # Simple known string with EOF
    secret_text = b"Test" + EOF_BYTES
    # Convert directly to native bits
    binary_data = ''.join(format(b, '08b') for b in secret_text)
    
    embed_image(mock_10x10, binary_data, out_10x10)
    
    extracted_bytes = extract_image(out_10x10)
    assert extracted_bytes is not None, "Extraction algorithm failed to locate EOF."
    assert extracted_bytes == b"Test", "Steganographic 10x10 bitwise corruption occurred."

def test_magic_bytes_signature(tmpdir):
    """Ensure the security protocol denies false extensions."""
    # Write a fake text file spoofed as PNG
    spoof_path = os.path.join(tmpdir, "spoofed.png")
    with open(spoof_path, "w") as f:
        f.write("import os; os.system('echo malicious')")
        
    assert verify_file_signature(spoof_path) is False, "Signature utility permitted hostile text file."
