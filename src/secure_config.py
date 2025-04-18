import os
from pathlib import Path
from cryptography.fernet import Fernet
import json
import base64
from typing import Optional, Tuple

class SecureConfig:
    def __init__(self):
        self.pepper_path = Path("/etc/alex-ai-agent/pepper")
        self.credentials_path = Path.home() / ".alex-ai-agent" / "credentials.enc"
        
        # Create directory if it doesn't exist
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        # Set directory permissions
        self.credentials_path.parent.chmod(0o700)  # Only owner can read/write/execute
        
        # Create empty credentials file if it doesn't exist
        if not self.credentials_path.exists():
            self.credentials_path.touch()
            self.credentials_path.chmod(0o600)  # Only owner can read/write
        
        # Load or generate encryption key
        self._load_or_generate_key()
    
    def _load_or_generate_key(self):
        """Load or generate the encryption key."""
        key_path = Path.home() / ".alex-ai-agent" / ".key"
        if key_path.exists():
            with open(key_path, "rb") as f:
                self.key = f.read()
        else:
            self.key = Fernet.generate_key()
            with open(key_path, "wb") as f:
                f.write(self.key)
            key_path.chmod(0o600)  # Only owner can read/write
    
    def _get_pepper(self) -> bytes:
        """Read the server-only pepper."""
        try:
            with open(self.pepper_path, "r") as f:
                return f.read().strip().encode()
        except FileNotFoundError:
            print(f"Warning: Pepper file not found at {self.pepper_path}")
            # Generate a temporary pepper if the file doesn't exist
            return os.urandom(32)  # 32 bytes = 256 bits
    
    def _encrypt_credentials(self, api_id: int, api_hash: str) -> str:
        """Encrypt credentials with pepper."""
        f = Fernet(self.key)
        data = json.dumps({
            "api_id": api_id,
            "api_hash": api_hash
        }).encode()
        # Add pepper to the data before encryption
        peppered_data = data + self._get_pepper()
        return f.encrypt(peppered_data).decode()
    
    def _decrypt_credentials(self, encrypted_data: str) -> Optional[Tuple[int, str]]:
        """Decrypt credentials and verify pepper."""
        try:
            f = Fernet(self.key)
            decrypted = f.decrypt(encrypted_data.encode())
            # Remove pepper from the end
            data = decrypted[:-len(self._get_pepper())]
            creds = json.loads(data)
            return creds["api_id"], creds["api_hash"]
        except Exception as e:
            print(f"Error decrypting credentials: {e}")
            return None
    
    def save_credentials(self, api_id: int, api_hash: str) -> bool:
        """Save encrypted credentials to file."""
        try:
            encrypted = self._encrypt_credentials(api_id, api_hash)
            with open(self.credentials_path, "w") as f:
                f.write(encrypted)
            return True
        except Exception as e:
            print(f"Error saving credentials: {e}")
            return False
    
    def load_credentials(self) -> Optional[Tuple[int, str]]:
        """Load and decrypt credentials from file."""
        if not self.credentials_path.exists():
            return None
        try:
            with open(self.credentials_path, "r") as f:
                encrypted = f.read()
                if not encrypted:  # Empty file
                    return None
            return self._decrypt_credentials(encrypted)
        except Exception as e:
            print(f"Error loading credentials: {e}")
            return None
    
    def credentials_exist(self) -> bool:
        """Check if credentials exist and are valid."""
        return self.load_credentials() is not None

# Create a singleton instance
secure_config = SecureConfig() 