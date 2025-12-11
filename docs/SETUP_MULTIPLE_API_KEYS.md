# How to Use Multiple API Keys

## Quick Setup Guide

### Step 1: Set Environment Variables

You can set multiple API keys. The system loads them in order:
- `GEMINI_API_KEY` → **Index 0** (base key)
- `GEMINI_API_KEY_1` → **Index 1**
- `GEMINI_API_KEY_2` → **Index 2**
- And so on...

#### For Gemini API (Google AI)

```bash
# Linux/Mac
export GEMINI_API_KEY="AIzaSyAbc123..."      # Index 0 (base)
export GEMINI_API_KEY_1="AIzaSyDef456..."    # Index 1
export GEMINI_API_KEY_2="AIzaSyGhi789..."    # Index 2
export GEMINI_API_KEY_3="AIzaSyJkl012..."    # Index 3

# Windows (Command Prompt)
set GEMINI_API_KEY_1=AIzaSyAbc123...
set GEMINI_API_KEY_2=AIzaSyDef456...
set GEMINI_API_KEY_3=AIzaSyGhi789...

# Windows (PowerShell)
$env:GEMINI_API_KEY_1="AIzaSyAbc123..."
$env:GEMINI_API_KEY_2="AIzaSyDef456..."
$env:GEMINI_API_KEY_3="AIzaSyGhi789..."
```

#### For NVIDIA API

```bash
# Linux/Mac
export NVIDIA_API_KEY_1="nvapi-abc123..."
export NVIDIA_API_KEY_2="nvapi-def456..."
export NVIDIA_API_KEY_3="nvapi-ghi789..."

# Windows (Command Prompt)
set NVIDIA_API_KEY_1=nvapi-abc123...
set NVIDIA_API_KEY_2=nvapi-def456...

# Windows (PowerShell)
$env:NVIDIA_API_KEY_1="nvapi-abc123..."
$env:NVIDIA_API_KEY_2="nvapi-def456..."
```

#### For OpenRouter API (Amazon Nova)

```bash
# Linux/Mac
export OPENROUTER_API_KEY_1="sk-or-v1-abc123..."
export OPENROUTER_API_KEY_2="sk-or-v1-def456..."
export OPENROUTER_API_KEY_3="sk-or-v1-ghi789..."

# Windows (Command Prompt)
set OPENROUTER_API_KEY_1=sk-or-v1-abc123...
set OPENROUTER_API_KEY_2=sk-or-v1-def456...

# Windows (PowerShell)
$env:OPENROUTER_API_KEY_1="sk-or-v1-abc123..."
$env:OPENROUTER_API_KEY_2="sk-or-v1-def456..."
```

### Step 2: Using .env File (Recommended - Already Configured!)

✅ **Good news:** The app already has .env support built-in!

Just create a `.env` file in your project root:

```bash
# .env file
# Gemini API Keys (get from: https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=AIzaSyAbc123...      # Index 0 (base key)
GEMINI_API_KEY_1=AIzaSyDef456...    # Index 1
GEMINI_API_KEY_2=AIzaSyGhi789...    # Index 2

# NVIDIA API Keys (get from: https://build.nvidia.com/)
NVIDIA_API_KEY=nvapi-abc123...      # Index 0 (base key)
NVIDIA_API_KEY_1=nvapi-def456...    # Index 1

# OpenRouter API Keys (get from: https://openrouter.ai/keys)
OPENROUTER_API_KEY=sk-or-v1-abc123...    # Index 0 (base key)
OPENROUTER_API_KEY_1=sk-or-v1-def456...  # Index 1
```

**That's it!** Just run the app normally:

```bash
python3 run.py
```

The .env file is automatically loaded. No extra steps needed!

**Quick Start:**
```bash
# 1. Copy the example file
cp .env.example .env

# 2. Edit .env and add your API keys
nano .env

# 3. Run the app
python3 run.py
```

### Step 3: Verify Keys Are Loaded

Run this to check if your keys are loaded correctly:

```python
python3 -c "
from api_key_manager import get_api_key_manager

manager = get_api_key_manager()
status = manager.get_all_services_status()

for service, info in status.items():
    print(f'{service.upper()}: {info[\"total_keys\"]} key(s) loaded')
"
```

Expected output:
```
NVIDIA: 2 key(s) loaded
GEMINI: 3 key(s) loaded
OPENROUTER: 2 key(s) loaded
```

---

## How It Works

### Automatic Rotation

Once you have multiple keys configured, the system automatically:

1. **Rotates** through them (round-robin)
2. **Fails over** when one key fails
3. **Blocks** keys that fail 3 times (for 5 minutes)
4. **Unblocks** keys after cooldown
5. **Tracks** success rates for each key

### Example Flow

Let's say you have 3 Gemini keys configured:

```
Request 1 → Uses Key 1 ✓
Request 2 → Uses Key 2 ✓
Request 3 → Uses Key 3 ✓
Request 4 → Uses Key 1 ✓ (rotation back to start)
Request 5 → Uses Key 2 ✗ (fails - rate limit)
Request 6 → Uses Key 3 ✓ (automatically switched)
Request 7 → Uses Key 1 ✓
Request 8 → Uses Key 2 ✗ (fails again - 2nd failure)
Request 9 → Uses Key 3 ✓ (automatically switched)
Request 10 → Uses Key 2 ✗ (fails again - 3rd failure, BLOCKED)
Request 11 → Uses Key 3 ✓ (Key 2 is skipped)
Request 12 → Uses Key 1 ✓
Request 13 → Uses Key 3 ✓ (Key 2 still blocked)
... 5 minutes later ...
Request N → Uses Key 2 ✓ (unblocked and back in rotation)
```

---

## Getting API Keys

### Gemini API (Google AI)
1. Go to https://aistudio.google.com/app/apikey
2. Click "Create API Key"
3. Copy the key (starts with `AIzaSy...`)
4. **Tip:** Create multiple keys from different Google accounts for more quota

### NVIDIA API
1. Go to https://build.nvidia.com/
2. Sign in and navigate to API Keys
3. Generate a new API key
4. Copy the key (starts with `nvapi-...`)

### OpenRouter API
1. Go to https://openrouter.ai/keys
2. Sign up and create an API key
3. Copy the key (starts with `sk-or-v1-...`)
4. **Tip:** OpenRouter gives free credits for Nova model

---

## Common Scenarios

### Scenario 1: Maximize Free Tier Usage

If you have multiple Google accounts, create one Gemini API key from each:

```bash
export GEMINI_API_KEY="key-from-account-1"      # Index 0
export GEMINI_API_KEY_1="key-from-account-2"    # Index 1
export GEMINI_API_KEY_2="key-from-account-3"    # Index 2
export GEMINI_API_KEY_3="key-from-account-4"    # Index 3
```

This gives you 4x the free tier quota!

### Scenario 2: Paid + Free Keys

Mix paid and free keys:

```bash
export GEMINI_API_KEY="paid-key-with-high-quota"    # Index 0 - tried first
export GEMINI_API_KEY_1="free-key-1"                # Index 1 - backup
export GEMINI_API_KEY_2="free-key-2"                # Index 2 - backup
```

The system will rotate through all of them, maximizing your available quota.

### Scenario 3: Single Key (Backward Compatible)

If you only have one key, the old method still works:

```bash
export GEMINI_API_KEY="your-single-key"
```

The system will use this single key without rotation.

---

## Troubleshooting

### Problem: Keys not being loaded

**Check:**
1. Environment variables are set in the same terminal/session where you run the app
2. Variable names match exactly (case-sensitive)
3. No extra spaces in variable values

**Test:**
```bash
# Linux/Mac
echo $GEMINI_API_KEY_1
echo $GEMINI_API_KEY_2

# Windows (Command Prompt)
echo %GEMINI_API_KEY_1%
echo %GEMINI_API_KEY_2%

# Windows (PowerShell)
echo $env:GEMINI_API_KEY_1
echo $env:GEMINI_API_KEY_2
```

### Problem: Only first key is being used

**Likely cause:** Other keys aren't set properly.

**Fix:** Verify all keys are loaded:
```python
import os
print("Key 1:", os.environ.get('GEMINI_API_KEY_1'))
print("Key 2:", os.environ.get('GEMINI_API_KEY_2'))
print("Key 3:", os.environ.get('GEMINI_API_KEY_3'))
```

### Problem: All keys get blocked quickly

**Causes:**
- Invalid API keys
- Insufficient quota/rate limits
- API service issues

**Fix:**
1. Verify each key works individually
2. Check your quota limits with the API provider
3. Add more keys to distribute the load
4. Increase wait times between requests

---

## Best Practices

✅ **Use at least 2-3 keys per service** for reliability  
✅ **Get keys from different accounts** to maximize free tier  
✅ **Keep backup keys** from different providers if possible  
✅ **Monitor key usage** to identify which ones work best  
✅ **Store keys securely** in .env file (add to .gitignore)  
✅ **Don't commit keys to git** - use environment variables  
✅ **Rotate keys periodically** for security  

---

## Advanced: Persistent Configuration

To make environment variables persist across reboots:

### Linux/Mac - Add to ~/.bashrc or ~/.zshrc:
```bash
export GEMINI_API_KEY_1="..."
export GEMINI_API_KEY_2="..."
export GEMINI_API_KEY_3="..."
```

Then reload: `source ~/.bashrc`

### Windows - Use System Environment Variables:
1. Search for "Environment Variables" in Start Menu
2. Click "Edit system environment variables"
3. Click "Environment Variables" button
4. Add your keys under "User variables"

---

## Summary

**To use multiple API keys:**

1. **Set numbered environment variables:**
   - `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, etc.
   - `NVIDIA_API_KEY_1`, `NVIDIA_API_KEY_2`, etc.
   - `OPENROUTER_API_KEY_1`, `OPENROUTER_API_KEY_2`, etc.

2. **That's it!** The system automatically:
   - Loads all keys
   - Rotates through them
   - Handles failures
   - Maximizes availability

No code changes needed - just set the environment variables and the API Key Manager handles everything!
