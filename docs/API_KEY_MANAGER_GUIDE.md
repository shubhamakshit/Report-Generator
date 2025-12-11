# API Key Manager Guide

## Overview

The API Key Manager provides automatic failover and rotation across multiple API keys for the same service. If one API key fails due to rate limiting or errors, the system automatically switches to a backup key.

## Features

✅ **Automatic Failover** - Switches to backup keys when one fails  
✅ **Load Balancing** - Rotates through keys using round-robin  
✅ **Failure Tracking** - Blocks keys after consecutive failures  
✅ **Auto-Recovery** - Unblocks keys after cooldown period  
✅ **Success Rate Monitoring** - Tracks performance of each key  
✅ **Thread-Safe** - Can be used in multi-threaded environments  

## Configuration

### Setting Up Multiple API Keys

You can configure multiple API keys for each service using environment variables:

#### Method 1: Numbered Keys (Recommended)
```bash
# Gemini API Keys
export GEMINI_API_KEY_1="your-first-gemini-key"
export GEMINI_API_KEY_2="your-second-gemini-key"
export GEMINI_API_KEY_3="your-third-gemini-key"

# NVIDIA API Keys
export NVIDIA_API_KEY_1="your-first-nvidia-key"
export NVIDIA_API_KEY_2="your-second-nvidia-key"

# OpenRouter API Keys (for Nova)
export OPENROUTER_API_KEY_1="your-first-openrouter-key"
export OPENROUTER_API_KEY_2="your-second-openrouter-key"
```

#### Method 2: Single Key (Backward Compatible)
```bash
export GEMINI_API_KEY="your-gemini-key"
export NVIDIA_API_KEY="your-nvidia-key"
export OPENROUTER_API_KEY="your-openrouter-key"
```

#### Method 3: Mixed (Both Work Together)
```bash
# These will all be combined into the pool
export GEMINI_API_KEY="key-1"
export GEMINI_API_KEY_1="key-2"
export GEMINI_API_KEY_2="key-3"
# Result: 3 keys total (duplicates are automatically removed)
```

### Supported Services

| Service | Environment Variable Pattern | Used For |
|---------|----------------------------|----------|
| `nvidia` | `NVIDIA_API_KEY` or `NVIDIA_API_KEY_1`, `NVIDIA_API_KEY_2`, etc. | OCR processing |
| `gemini` | `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or numbered variants | Question classification, Q&A extraction |
| `openrouter` | `OPENROUTER_API_KEY` or `OPENROUTER_API_KEY_1`, etc. | Amazon Nova classification |

## How It Works

### 1. Key Rotation
Keys are automatically rotated using round-robin:
```
Request 1 → Key 1
Request 2 → Key 2  
Request 3 → Key 3
Request 4 → Key 1 (back to start)
```

### 2. Failure Handling
When a key fails:
- Failure count is incremented
- After **3 consecutive failures**, the key is **blocked for 5 minutes**
- System automatically switches to next available key
- After cooldown period, key is automatically unblocked

### 3. Success Tracking
When a key succeeds:
- Success count is incremented
- Failure count is reset to 0
- Key is marked as available
- System rotates to next key for load balancing

## Usage in Code

### Automatic (Already Integrated)

The API Key Manager is already integrated into:
- ✅ `gemini_classifier.py` - Gemini question classification
- ✅ `nova_classifier.py` - Nova question classification  
- ✅ `processing.py` - NVIDIA OCR API

**No code changes needed!** Just set up multiple API keys and the system handles the rest.

### Manual Usage (Advanced)

If you need to add API key management to other modules:

```python
from api_key_manager import get_api_key_manager

# Get the manager instance
manager = get_api_key_manager()

# Get an API key
api_key, key_index = manager.get_key('gemini')

if api_key:
    try:
        # Make your API call
        response = make_api_call(api_key)
        
        # Mark as successful
        manager.mark_success('gemini', key_index)
        
    except Exception as e:
        # Mark as failed (will block after 3 failures)
        manager.mark_failure('gemini', key_index)
else:
    print("No API keys available!")
```

## Monitoring

### Get Service Status

```python
from api_key_manager import get_api_key_manager

manager = get_api_key_manager()

# Get status for one service
status = manager.get_service_status('gemini')
print(f"Available keys: {status['available_keys']}/{status['total_keys']}")
print(f"Blocked keys: {status['blocked_keys']}")

# Get status for all services
all_status = manager.get_all_services_status()
for service, info in all_status.items():
    print(f"{service}: {info['available_keys']}/{info['total_keys']} keys available")
```

### Example Output
```json
{
  "service": "gemini",
  "available": true,
  "total_keys": 3,
  "available_keys": 2,
  "blocked_keys": 1,
  "keys": [
    {
      "index": 0,
      "is_available": true,
      "is_blocked": false,
      "failure_count": 0,
      "total_requests": 15,
      "success_rate": 100.0,
      "blocked_until": null
    },
    {
      "index": 1,
      "is_available": true,
      "is_blocked": false,
      "failure_count": 0,
      "total_requests": 12,
      "success_rate": 100.0,
      "blocked_until": null
    },
    {
      "index": 2,
      "is_available": false,
      "is_blocked": true,
      "failure_count": 3,
      "total_requests": 8,
      "success_rate": 62.5,
      "blocked_until": "2025-12-08T04:30:00.000000"
    }
  ]
}
```

## Configuration Options

### Block Duration

By default, keys are blocked for **5 minutes** after 3 failures. You can customize this:

```python
# Block for 10 minutes instead
manager.mark_failure('gemini', key_index, block_duration_minutes=10)
```

### Failure Threshold

The failure threshold is currently hardcoded to **3 consecutive failures**. This is defined in `api_key_manager.py` in the `mark_failure()` method:

```python
if self.failure_count >= 3:
    self.is_blocked = True
```

## Troubleshooting

### Problem: "No API keys available"

**Cause:** All keys are blocked or no keys are configured.

**Solution:**
1. Check environment variables are set correctly
2. Wait for cooldown period (5 minutes)
3. Manually reset the service:
   ```python
   manager.reset_service('gemini')
   ```

### Problem: Keys getting blocked frequently

**Cause:** Rate limiting or invalid API keys.

**Solution:**
1. Check API key validity
2. Verify rate limits with your API provider
3. Add more API keys to distribute load
4. Increase block duration to avoid rapid retries

### Problem: Not using multiple keys even though they're configured

**Cause:** Check if keys are being loaded correctly.

**Solution:**
```python
manager = get_api_key_manager()
status = manager.get_service_status('gemini')
print(f"Total keys loaded: {status['total_keys']}")
```

## Best Practices

1. **Use at least 2-3 keys per service** for better reliability
2. **Monitor success rates** to identify problematic keys
3. **Stagger API requests** to avoid hitting rate limits
4. **Keep backup keys from different accounts** if possible
5. **Test keys periodically** to ensure they're still valid

## Logging

The API Key Manager logs important events:

```
INFO: Loaded API keys: NVIDIA=2, Gemini=3, OpenRouter=2
INFO: Registered 3 API key(s) for service: gemini
DEBUG: Using API key 1/3 for gemini
DEBUG: API key 1 for gemini marked as successful
WARNING: API key 2 for gemini marked as failed
WARNING: API key for gemini blocked until 2025-12-08 04:30:00 after 3 failures
INFO: API key for gemini unblocked after cooldown period
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    API Key Manager                           │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Service: nvidia        Service: gemini    Service: openrouter│
│  ┌──────────────┐      ┌──────────────┐   ┌──────────────┐  │
│  │ Key 1 ✓      │      │ Key 1 ✓      │   │ Key 1 ✓      │  │
│  │ Key 2 ✓      │      │ Key 2 ✓      │   │ Key 2 ✓      │  │
│  └──────────────┘      │ Key 3 ✗      │   └──────────────┘  │
│                        │ (blocked)    │                      │
│                        └──────────────┘                      │
│                                                               │
│  Features:                                                    │
│  • Round-robin rotation                                      │
│  • Automatic failover                                        │
│  • Failure tracking                                          │
│  • Auto-recovery after cooldown                              │
│                                                               │
└─────────────────────────────────────────────────────────────┘
         │              │              │
         ▼              ▼              ▼
   processing.py   gemini_classifier.py   nova_classifier.py
   (NVIDIA OCR)    (Gemini AI)           (Amazon Nova)
```

## Future Enhancements

Potential improvements for the API Key Manager:

- [ ] Web dashboard for monitoring key status
- [ ] Configurable failure threshold per service
- [ ] Exponential backoff for blocked keys
- [ ] API key health checks
- [ ] Cost tracking per key
- [ ] Rate limit detection and adaptive throttling
- [ ] Database persistence for key statistics
- [ ] Email alerts when all keys are blocked
- [ ] Integration with settings page for user-visible status
