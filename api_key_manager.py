"""
Unified API Key Manager with automatic failover and rotation.

This module manages multiple API keys for each service and automatically
switches to backup keys when one fails due to rate limiting or errors.
"""

import os
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import threading
import logging

logger = logging.getLogger(__name__)

@dataclass
class APIKeyStatus:
    """Tracks the status of an individual API key."""
    key: str
    service: str
    last_used: Optional[datetime] = None
    failure_count: int = 0
    last_failure: Optional[datetime] = None
    is_blocked: bool = False
    blocked_until: Optional[datetime] = None
    total_requests: int = 0
    successful_requests: int = 0
    
    def mark_success(self):
        """Mark a successful API call."""
        self.last_used = datetime.now()
        self.total_requests += 1
        self.successful_requests += 1
        self.failure_count = 0  # Reset failure count on success
        self.is_blocked = False
        self.blocked_until = None
    
    def mark_failure(self, block_duration_minutes: int = 5):
        """Mark a failed API call and potentially block the key."""
        self.last_used = datetime.now()
        self.last_failure = datetime.now()
        self.total_requests += 1
        self.failure_count += 1
        
        # Block key after 3 consecutive failures
        if self.failure_count >= 3:
            self.is_blocked = True
            self.blocked_until = datetime.now() + timedelta(minutes=block_duration_minutes)
            logger.warning(f"API key for {self.service} blocked until {self.blocked_until} after {self.failure_count} failures")
    
    def is_available(self) -> bool:
        """Check if this key is available for use."""
        if not self.is_blocked:
            return True
        
        # Check if block has expired
        if self.blocked_until and datetime.now() > self.blocked_until:
            self.is_blocked = False
            self.blocked_until = None
            self.failure_count = 0
            logger.info(f"API key for {self.service} unblocked after cooldown period")
            return True
        
        return False
    
    def get_success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_requests == 0:
            return 100.0
        return (self.successful_requests / self.total_requests) * 100


class APIKeyManager:
    """
    Manages multiple API keys for different services with automatic failover.
    
    Supports multiple keys per service and automatically rotates to backup keys
    when one fails or hits rate limits.
    """
    
    def __init__(self):
        self.keys: Dict[str, List[APIKeyStatus]] = {}
        self.current_index: Dict[str, int] = {}
        self.lock = threading.Lock()
        self._load_keys_from_env()
    
    def _load_keys_from_env(self):
        """Load API keys from environment variables."""
        
        # NVIDIA API Keys
        nvidia_keys = self._get_keys_from_env('NVIDIA_API_KEY')
        if nvidia_keys:
            self.register_service('nvidia', nvidia_keys)
        
        # Gemini API Keys
        gemini_keys = self._get_keys_from_env('GEMINI_API_KEY')
        google_keys = self._get_keys_from_env('GOOGLE_API_KEY')
        all_gemini_keys = gemini_keys + google_keys
        if all_gemini_keys:
            self.register_service('gemini', all_gemini_keys)
        
        # OpenRouter API Keys (for Nova)
        openrouter_keys = self._get_keys_from_env('OPENROUTER_API_KEY')
        if openrouter_keys:
            self.register_service('openrouter', openrouter_keys)
        
        logger.info(f"Loaded API keys: NVIDIA={len(nvidia_keys)}, Gemini={len(all_gemini_keys)}, OpenRouter={len(openrouter_keys)}")
    
    def _get_keys_from_env(self, base_name: str) -> List[str]:
        """
        Get API keys from environment variables.
        Loads keys in order:
        1. BASE_NAME (as index 0)
        2. BASE_NAME_1, BASE_NAME_2, BASE_NAME_3, etc. (as indices 1, 2, 3...)
        
        Example:
        - GEMINI_API_KEY      → index 0
        - GEMINI_API_KEY_1    → index 1
        - GEMINI_API_KEY_2    → index 2
        """
        keys = []
        
        # First, try base key (index 0)
        base_key = os.environ.get(base_name)
        if base_key:
            keys.append(base_key)
        
        # Then try numbered keys (1-10)
        for i in range(1, 11):
            numbered_key = os.environ.get(f"{base_name}_{i}")
            if numbered_key:
                keys.append(numbered_key)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_keys = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                unique_keys.append(key)
        
        return unique_keys
    
    def register_service(self, service: str, api_keys: List[str]):
        """Register multiple API keys for a service."""
        with self.lock:
            self.keys[service] = [
                APIKeyStatus(key=key, service=service) 
                for key in api_keys
            ]
            self.current_index[service] = 0
            logger.info(f"Registered {len(api_keys)} API key(s) for service: {service}")
    
    def get_key(self, service: str) -> Optional[Tuple[str, int]]:
        """
        Get an available API key for the specified service.
        Returns (api_key, key_index) or (None, -1) if no keys available.
        """
        with self.lock:
            if service not in self.keys or not self.keys[service]:
                logger.warning(f"No API keys registered for service: {service}")
                return None, -1
            
            service_keys = self.keys[service]
            start_index = self.current_index[service]
            
            # Try to find an available key, starting from current index
            for attempt in range(len(service_keys)):
                current_idx = (start_index + attempt) % len(service_keys)
                key_status = service_keys[current_idx]
                
                if key_status.is_available():
                    self.current_index[service] = current_idx
                    logger.debug(f"Using API key {current_idx + 1}/{len(service_keys)} for {service}")
                    return key_status.key, current_idx
            
            # All keys are blocked
            logger.error(f"All API keys for {service} are currently blocked or unavailable")
            return None, -1
    
    def mark_success(self, service: str, key_index: int):
        """Mark an API call as successful."""
        with self.lock:
            if service in self.keys and 0 <= key_index < len(self.keys[service]):
                self.keys[service][key_index].mark_success()
                logger.debug(f"API key {key_index + 1} for {service} marked as successful")
                
                # Move to next key for load balancing (round-robin)
                self.current_index[service] = (key_index + 1) % len(self.keys[service])
    
    def mark_failure(self, service: str, key_index: int, block_duration_minutes: int = 5):
        """Mark an API call as failed and potentially block the key."""
        with self.lock:
            if service in self.keys and 0 <= key_index < len(self.keys[service]):
                self.keys[service][key_index].mark_failure(block_duration_minutes)
                logger.warning(f"API key {key_index + 1} for {service} marked as failed")
                
                # Move to next key immediately
                self.current_index[service] = (key_index + 1) % len(self.keys[service])
    
    def get_service_status(self, service: str) -> Dict:
        """Get status information for a service."""
        with self.lock:
            if service not in self.keys:
                return {
                    'service': service,
                    'available': False,
                    'total_keys': 0,
                    'available_keys': 0,
                    'blocked_keys': 0
                }
            
            service_keys = self.keys[service]
            available_keys = sum(1 for k in service_keys if k.is_available())
            blocked_keys = sum(1 for k in service_keys if k.is_blocked)
            
            return {
                'service': service,
                'available': available_keys > 0,
                'total_keys': len(service_keys),
                'available_keys': available_keys,
                'blocked_keys': blocked_keys,
                'keys': [
                    {
                        'index': i,
                        'is_available': k.is_available(),
                        'is_blocked': k.is_blocked,
                        'failure_count': k.failure_count,
                        'total_requests': k.total_requests,
                        'success_rate': round(k.get_success_rate(), 2),
                        'blocked_until': k.blocked_until.isoformat() if k.blocked_until else None
                    }
                    for i, k in enumerate(service_keys)
                ]
            }
    
    def get_all_services_status(self) -> Dict[str, Dict]:
        """Get status for all registered services."""
        return {
            service: self.get_service_status(service)
            for service in self.keys.keys()
        }
    
    def reset_service(self, service: str):
        """Reset all keys for a service (unblock and clear stats)."""
        with self.lock:
            if service in self.keys:
                for key_status in self.keys[service]:
                    key_status.is_blocked = False
                    key_status.blocked_until = None
                    key_status.failure_count = 0
                logger.info(f"Reset all keys for service: {service}")


# Global singleton instance
_api_key_manager = None

def get_api_key_manager() -> APIKeyManager:
    """Get the global API key manager instance."""
    global _api_key_manager
    if _api_key_manager is None:
        _api_key_manager = APIKeyManager()
    return _api_key_manager
